import uuid
from datetime import datetime, time, timedelta, timezone
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.v1.endpoints.widget import _build_config_response
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.booking import BusinessSchedule, ScheduleDay
from app.models.session import ChatSession
from app.models.message import ChatMessage
from app.models.system_alert import SystemAlert


def _make_org() -> Organization:
    return Organization(
        id=uuid.uuid4(),
        name="Widget Org",
        api_key=f"ds_test_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )


class TestWidgetMeetingPolicy:
    def test_online_only_derives_online_available_type(self):
        org = _make_org()
        config = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            custom_prompts={},
            meeting_mode="ONLINE_ONLY",
            allow_hybrid_selection=False,
            meeting_provider_default="NONE",
        )

        body = _build_config_response(org, config)
        assert body["meeting_mode"] == "ONLINE_ONLY"
        assert body["allow_hybrid_selection"] is False
        assert body["available_meeting_types"] == ["ONLINE"]

    def test_offline_only_derives_offline_available_type(self):
        org = _make_org()
        config = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            custom_prompts={},
            meeting_mode="OFFLINE_ONLY",
            allow_hybrid_selection=True,
            meeting_provider_default="NONE",
        )

        body = _build_config_response(org, config)
        assert body["meeting_mode"] == "OFFLINE_ONLY"
        assert body["available_meeting_types"] == ["OFFLINE"]

    def test_hybrid_with_explicit_selection_derives_both(self):
        org = _make_org()
        config = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            custom_prompts={},
            meeting_mode="HYBRID",
            allow_hybrid_selection=True,
            meeting_provider_default="GOOGLE_MEET",
        )

        body = _build_config_response(org, config)
        assert body["meeting_mode"] == "HYBRID"
        assert body["meeting_provider_default"] == "GOOGLE_MEET"
        assert body["available_meeting_types"] == ["ONLINE", "OFFLINE"]

    def test_hybrid_without_selection_defaults_offline(self):
        org = _make_org()
        config = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            custom_prompts={},
            meeting_mode="HYBRID",
            allow_hybrid_selection=False,
            meeting_provider_default="NONE",
        )

        body = _build_config_response(org, config)
        assert body["meeting_mode"] == "HYBRID"
        assert body["allow_hybrid_selection"] is False
        assert body["available_meeting_types"] == ["OFFLINE"]


class TestWidgetHistory:
    async def test_widget_history_sanitizes_structured_agent_json(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = Organization(
            name="History Org",
            api_key=f"ds_widget_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org)
        await db_session.flush()

        cfg = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            widget_token=f"ds_wid_{uuid.uuid4().hex[:24]}",
        )
        db_session.add(cfg)
        await db_session.flush()

        session = ChatSession(org_id=org.id, channel="web", state_data={})
        db_session.add(session)
        await db_session.flush()

        db_session.add(
            ChatMessage(
                session_id=session.id,
                role="agent",
                content='{"response_text":"Hello! How can I assist you today?","extracted_fields":{},"user_intent":"proceed","confidence":0.2}',
            )
        )
        await db_session.commit()

        resp = await client.get(
            "/api/v1/widget/history",
            params={"session_id": str(session.id), "widget_token": cfg.widget_token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["content"] == "Hello! How can I assist you today?"


class TestWidgetAvailability:
    async def _seed_org_and_schedule(self, db_session):
        org = Organization(
            name="Availability Org",
            api_key=f"ds_widget_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org)
        await db_session.flush()

        config = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            timezone="America/New_York",
            widget_token=f"ds_wid_{uuid.uuid4().hex[:24]}",
        )
        db_session.add(config)

        schedule = BusinessSchedule(
            org_id=org.id,
            slot_duration_minutes=60,
            booking_window_days=60,
            timezone="America/New_York",
        )
        db_session.add(schedule)

        monday = ScheduleDay(
            org_id=org.id,
            day_of_week=0,
            is_active=True,
            open_time=time(14, 0),
            close_time=time(16, 0),
            break_start=None,
            break_end=None,
        )
        db_session.add(monday)
        await db_session.commit()
        await db_session.refresh(org)
        await db_session.refresh(config)
        return org, config

    async def _ensure_schedule_day(self, db_session, org_id, target_date, open_time, close_time):
        result = await db_session.execute(
            select(ScheduleDay).where(
                ScheduleDay.org_id == org_id,
                ScheduleDay.day_of_week == target_date.weekday(),
            )
        )
        day = result.scalars().first()
        if day:
            day.is_active = True
            day.open_time = open_time
            day.close_time = close_time
            day.break_start = None
            day.break_end = None
            return
        db_session.add(
            ScheduleDay(
                org_id=org_id,
                day_of_week=target_date.weekday(),
                is_active=True,
                open_time=open_time,
                close_time=close_time,
                break_start=None,
                break_end=None,
            )
        )

    async def test_widget_availability_valid_timezone_returns_dual_time_fields(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-01-07",
                "tz": "Asia/Kolkata",
                "timezone_source": "user_selected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2030-01-07"
        assert body["org_timezone"] == "America/New_York"
        assert body["user_timezone"] == "Asia/Kolkata"
        assert body["timezone_source"] == "user_selected"
        assert body["timezone_fallback_used"] is False
        assert body["timezone_fallback_reason"] is None
        assert len(body["slots"]) >= 1

        first = body["slots"][0]
        assert first["slot_id"].startswith("virtual|")
        assert first["label_user_tz"]
        assert "T" in first["start_user_tz"]
        assert "T" in first["end_user_tz"]
        assert "T" in first["start_org_tz"]
        assert "T" in first["end_org_tz"]
        assert first["start_utc"].endswith("+00:00")
        assert first["end_utc"].endswith("+00:00")

    async def test_widget_availability_missing_timezone_falls_back_to_org_timezone(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-01-07",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_timezone"] == "America/New_York"
        assert body["timezone_source"] == "fallback_org"
        assert body["timezone_fallback_used"] is True
        assert body["timezone_fallback_reason"] == "missing_tz"

    async def test_widget_availability_invalid_timezone_falls_back_to_org_timezone(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-01-07",
                "tz": "Mars/Phobos",
                "timezone_source": "detected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_timezone"] == "America/New_York"
        assert body["timezone_source"] == "fallback_org"
        assert body["timezone_fallback_used"] is True
        assert body["timezone_fallback_reason"] == "invalid_tz"

    async def test_widget_availability_includes_status_and_slot_availability_flags(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-01-07",
                "tz": "Asia/Kolkata",
                "timezone_source": "user_selected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["availability_status"] == "ok"
        assert len(body["slots"]) >= 1
        for slot in body["slots"]:
            assert "available" in slot
            assert slot["available"] is True
            assert slot["reason"] is None

    async def test_widget_availability_closed_day_returns_closed_status(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        # Sunday (0=Mon in schedule seed, so Sunday has no active schedule)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-01-06",
                "tz": "Asia/Kolkata",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["availability_status"] == "closed_day"
        assert body["slots"] == []

    async def test_widget_availability_fallback_emits_comm_03_alert_without_pii(
        self, client, db_session
    ):
        org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-01-07",
            },
            headers={
                "x-request-id": "req-widget-fallback",
                "x-trace-id": "trace-widget-fallback",
            },
        )
        assert resp.status_code == 200

        rows = await db_session.execute(
            select(SystemAlert)
            .where(SystemAlert.org_id == org.id)
            .where(SystemAlert.code == "TIMEZONE_FALLBACK_MISSING_TZ")
        )
        alert = rows.scalars().first()
        assert alert is not None
        assert alert.component == "timezone"
        assert alert.request_id is not None
        assert alert.trace_id == "trace-widget-fallback"
        assert (alert.context or {}).get("reason") == "missing_tz"
        assert (alert.context or {}).get("timezone_source") == "fallback_org"
        assert "email" not in (alert.context or {})
        assert "phone" not in (alert.context or {})
        assert "name" not in (alert.context or {})
        assert "token" not in (alert.context or {})

    async def test_widget_availability_dst_spring_forward_keeps_canonical_slot_identity(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-03-11",
                "tz": "America/Los_Angeles",
                "timezone_source": "user_selected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["org_timezone"] == "America/New_York"
        assert body["user_timezone"] == "America/Los_Angeles"
        assert body["timezone_fallback_used"] is False
        assert body["timezone_fallback_reason"] is None
        assert len(body["slots"]) >= 1

        seen_start_utc = set()
        for slot in body["slots"]:
            assert slot["slot_id"].startswith("virtual|")
            assert slot["start_utc"].endswith("+00:00")
            assert slot["start_org_tz"].endswith("-04:00")
            seen_start_utc.add(slot["start_utc"])

        assert len(seen_start_utc) == len(body["slots"])

    async def test_widget_availability_dst_fall_back_preserves_unique_utc_identity(
        self, client, db_session
    ):
        _org, cfg = await self._seed_org_and_schedule(db_session)
        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": "2030-11-04",
                "tz": "America/Los_Angeles",
                "timezone_source": "detected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["org_timezone"] == "America/New_York"
        assert body["user_timezone"] == "America/Los_Angeles"
        assert body["timezone_source"] == "detected"
        assert body["timezone_fallback_used"] is False
        assert len(body["slots"]) >= 1

        seen_slot_ids = set()
        seen_start_utc = set()
        for slot in body["slots"]:
            assert slot["slot_id"].startswith("virtual|")
            assert slot["start_utc"].endswith("+00:00")
            assert slot["start_org_tz"].endswith("-05:00")
            seen_slot_ids.add(slot["slot_id"])
            seen_start_utc.add(slot["start_utc"])

        assert len(seen_slot_ids) == len(body["slots"])
        assert len(seen_start_utc) == len(body["slots"])

    async def test_widget_availability_same_day_disabled_returns_policy_reason_code(
        self, client, db_session
    ):
        org, cfg = await self._seed_org_and_schedule(db_session)
        cfg.timezone = "UTC"
        cfg.allow_same_day_booking = False
        cfg.slot_cutoff_minutes = 5
        schedule = (
            await db_session.execute(
                select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
            )
        ).scalars().first()
        schedule.timezone = "UTC"

        now_utc = datetime.now(timezone.utc)
        target_date = now_utc.date()
        await self._ensure_schedule_day(
            db_session,
            org.id,
            target_date,
            open_time=(now_utc + timedelta(hours=2)).time().replace(second=0, microsecond=0),
            close_time=(now_utc + timedelta(hours=4)).time().replace(second=0, microsecond=0),
        )
        await db_session.commit()

        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": target_date.isoformat(),
                "tz": "UTC",
                "timezone_source": "detected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["availability_status"] == "same_day_disabled"
        assert body["policy_reason_code"] == "same_day_disabled"
        assert body["slots"] == []

    async def test_widget_availability_effective_timezone_cutoff_marks_slot_unavailable(
        self, client, db_session
    ):
        org, cfg = await self._seed_org_and_schedule(db_session)
        cfg.timezone = "UTC"
        cfg.allow_same_day_booking = True
        cfg.slot_cutoff_minutes = 5
        schedule = (
            await db_session.execute(
                select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
            )
        ).scalars().first()
        schedule.timezone = "UTC"

        now_utc = datetime.now(timezone.utc)
        target_date = now_utc.date()
        start_local = (now_utc + timedelta(minutes=2)).time().replace(second=0, microsecond=0)
        close_local = (now_utc + timedelta(minutes=62)).time().replace(
            second=0, microsecond=0
        )
        await self._ensure_schedule_day(
            db_session, org.id, target_date, open_time=start_local, close_time=close_local
        )
        await db_session.commit()

        resp = await client.get(
            "/api/v1/widget/availability",
            params={
                "widget_token": cfg.widget_token,
                "date": target_date.isoformat(),
                "tz": "UTC",
                "timezone_source": "detected",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["policy_reason_code"] == "past_time_cutoff"
        assert body["availability_status"] == "fully_booked"
        assert len(body["slots"]) >= 1
        first = body["slots"][0]
        assert first["available"] is False
        assert first["reason"] == "past_time_cutoff"
