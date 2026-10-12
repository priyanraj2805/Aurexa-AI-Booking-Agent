"""
Booking and slot tests — CRUD, tenant isolation, race conditions.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from zoneinfo import ZoneInfo

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.booking import TimeSlot, Booking, BusinessSchedule, ScheduleDay
from app.models.session import ChatSession

CSRF = {"X-Requested-With": "XMLHttpRequest"}


async def _make_org(db: AsyncSession, name="Booking Org") -> Organization:
    org = Organization(
        name=name,
        api_key=f"ds_bk_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db.add(org)
    await db.flush()

    config = OrgConfig(
        org_id=org.id,
        required_fields=["name", "email", "phone", "date", "time"],
    )
    db.add(config)
    await db.commit()
    await db.refresh(org)
    return org


async def _make_slot(
    db: AsyncSession, org_id, start_offset_hours=24, booked=False
) -> TimeSlot:
    start = datetime.now(timezone.utc) + timedelta(hours=start_offset_hours)
    slot = TimeSlot(
        org_id=org_id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        is_booked=booked,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return slot


class TestSlotCRUD:
    async def test_create_slot(self, client: AsyncClient, db_session: AsyncSession):
        org = await _make_org(db_session)
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=1, hours=1)).isoformat()

        resp = await client.post(
            "/api/v1/slots/",
            json={"start_time": start, "end_time": end},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["org_id"] == str(org.id)
        assert body["is_booked"] is False

    async def test_list_slots(self, client: AsyncClient, db_session: AsyncSession):
        org = await _make_org(db_session, "List Org")
        await _make_slot(db_session, org.id, start_offset_hours=48)
        await _make_slot(db_session, org.id, start_offset_hours=72)

        resp = await client.get(
            "/api/v1/slots/",
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    async def test_delete_slot(self, client: AsyncClient, db_session: AsyncSession):
        org = await _make_org(db_session, "Delete Org")
        slot = await _make_slot(db_session, org.id, start_offset_hours=96)

        resp = await client.delete(
            f"/api/v1/slots/{slot.id}",
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 204

    async def test_delete_slot_wrong_org(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org_a = await _make_org(db_session, "Org A")
        org_b = await _make_org(db_session, "Org B")
        slot = await _make_slot(db_session, org_a.id, start_offset_hours=120)

        resp = await client.delete(
            f"/api/v1/slots/{slot.id}",
            headers={"X-Org-API-Key": org_b.api_key},
        )
        assert resp.status_code == 404  # Org B can't see Org A's slot

    async def test_invalid_api_key(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/slots/",
            headers={"X-Org-API-Key": "ds_invalid"},
        )
        assert resp.status_code == 403


class TestBookingCRUD:
    """Test the booking attempt function via crud module (unit-level)."""

    async def test_attempt_booking_success(self, db_session: AsyncSession):
        from app.crud.booking import attempt_booking

        org = await _make_org(db_session, "Book Org")
        slot = await _make_slot(db_session, org.id)

        # Create a session for the booking
        session = ChatSession(
            org_id=org.id,
            state_data={},
            missing_fields=[],
            session_status="CONFIRMING",
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        booking = await attempt_booking(
            db=db_session,
            org_id=str(org.id),
            slot_id=str(slot.id),
            session_id=str(session.id),
            customer_name="John Doe",
            customer_email="john@test.com",
            customer_phone="+1234567890",
        )
        assert booking is not None
        assert booking.customer_name == "John Doe"

    async def test_attempt_booking_already_booked(self, db_session: AsyncSession):
        from app.crud.booking import attempt_booking

        org = await _make_org(db_session, "Booked Org")
        slot = await _make_slot(db_session, org.id, booked=True)

        session = ChatSession(
            org_id=org.id, state_data={}, missing_fields=[], session_status="CONFIRMING"
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        with pytest.raises(ValueError, match="no longer available"):
            await attempt_booking(
                db=db_session,
                org_id=str(org.id),
                slot_id=str(slot.id),
                session_id=str(session.id),
                customer_name="Jane",
                customer_email="jane@test.com",
            )

    async def test_attempt_booking_persists_timezone_snapshots(
        self, db_session: AsyncSession
    ):
        from app.crud.booking import attempt_booking

        org = await _make_org(db_session, "TZ Snapshot Org")
        slot = await _make_slot(db_session, org.id)

        session = ChatSession(
            org_id=org.id,
            state_data={},
            missing_fields=[],
            session_status="CONFIRMING",
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        user_zone = ZoneInfo("Asia/Kolkata")
        org_zone = ZoneInfo("America/New_York")
        user_start = slot.start_time.astimezone(user_zone)
        user_end = slot.end_time.astimezone(user_zone)
        org_start = slot.start_time.astimezone(org_zone)
        org_end = slot.end_time.astimezone(org_zone)

        booking = await attempt_booking(
            db=db_session,
            org_id=str(org.id),
            slot_id=str(slot.id),
            session_id=str(session.id),
            customer_name="Timezone User",
            customer_email="tz@test.com",
            customer_phone="+1234567890",
            user_timezone="Asia/Kolkata",
            user_slot_start_at=user_start,
            user_slot_end_at=user_end,
            timezone_source="user_selected",
            org_timezone_snapshot="America/New_York",
            org_slot_start_at=org_start,
            org_slot_end_at=org_end,
        )
        await db_session.refresh(booking)

        assert booking.user_timezone == "Asia/Kolkata"
        assert booking.timezone_source == "user_selected"
        assert booking.org_timezone_snapshot == "America/New_York"
        assert booking.user_slot_start_at is not None
        assert booking.user_slot_end_at is not None
        assert booking.org_slot_start_at is not None
        assert booking.org_slot_end_at is not None

    async def test_attempt_booking_cross_tenant_blocked(self, db_session: AsyncSession):
        """CR-02: A booking attempt against another org's slot should fail."""
        from app.crud.booking import attempt_booking

        org_a = await _make_org(db_session, "Org Owner")
        org_b = await _make_org(db_session, "Org Attacker")
        slot = await _make_slot(db_session, org_a.id)

        session = ChatSession(
            org_id=org_b.id,
            state_data={},
            missing_fields=[],
            session_status="CONFIRMING",
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        with pytest.raises(ValueError, match="not found|does not belong"):
            await attempt_booking(
                db=db_session,
                org_id=str(org_b.id),  # Attacker org
                slot_id=str(slot.id),  # Victim's slot
                session_id=str(session.id),
                customer_name="Hacker",
                customer_email="hacker@evil.com",
            )


class TestTimezonePolicyCommitPath:
    @staticmethod
    def _stub_graph_confirm_path(monkeypatch) -> None:
        async def _fake_ainvoke(inputs, config=None):
            return {
                "messages": [AIMessage(content="Confirmed.")],
                "collected_fields": dict(inputs.get("collected_fields") or {}),
                "follow_up_fields": [],
                "current_intent": "CONFIRM_BOOKING",
                "captured_deltas": {},
                "merge_diagnostics": {},
                "validation_errors": {},
                "context_window": [],
                "summary": "",
            }

        monkeypatch.setattr(
            "app.engine.langgraph_orchestrator.booking_agent.ainvoke",
            _fake_ainvoke,
        )

    @staticmethod
    def _freeze_clock(monkeypatch, frozen: datetime) -> None:
        """Pin the app's wall clock so slot/policy math is run-time independent.

        Module-level patches only — never replace ``datetime.datetime`` itself,
        which breaks SQLAlchemy's isinstance checks. All app modules that read
        the clock in the booking flow import datetime at module level, so the
        patches always apply.
        """
        import app.engine.message_processor as mp_mod
        import app.engine.scheduling_policy as sp_mod
        import app.engine.slot_engine as se_mod

        class _FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.astimezone(tz)

        monkeypatch.setattr(mp_mod, "datetime", _FrozenDateTime)
        monkeypatch.setattr(se_mod, "datetime", _FrozenDateTime)
        monkeypatch.setattr(sp_mod, "datetime", _FrozenDateTime)

    async def _prepare_confirm_session(
        self,
        db_session: AsyncSession,
        *,
        org_timezone: str,
        allow_same_day_booking: bool,
        slot_cutoff_minutes: int,
        booking_date: str,
        booking_time: str,
        user_timezone: str = "UTC",
    ) -> tuple[Organization, ChatSession]:
        org = await _make_org(db_session, "Policy Commit Org")
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.timezone = org_timezone
        cfg.allow_same_day_booking = allow_same_day_booking
        cfg.slot_cutoff_minutes = slot_cutoff_minutes
        db_session.add(
            BusinessSchedule(
                org_id=org.id,
                slot_duration_minutes=60,
                booking_window_days=60,
                timezone=org_timezone,
            )
        )
        db_session.add(
            ScheduleDay(
                org_id=org.id,
                day_of_week=date.fromisoformat(booking_date).weekday(),
                is_active=True,
                open_time=time.min,
                close_time=time(23, 59),
                break_start=None,
                break_end=None,
            )
        )
        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Policy User",
                "email": "policy@test.com",
                "phone": "5551112233",
                "date": booking_date,
                "time": booking_time,
                "user_timezone": user_timezone,
                "timezone_source": "user_selected",
                # New contract: OTP/booking only after the review card went
                # out. These tests exercise the commit path itself, so the
                # session starts as if the customer already saw the summary.
                "_review_presented": True,
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        return org, session

    async def test_confirm_booking_same_day_disabled_returns_policy_reason_and_no_commit(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        self._stub_graph_confirm_path(monkeypatch)
        now_utc = datetime.now(timezone.utc)
        booking_time = (now_utc + timedelta(hours=2)).strftime("%H:%M")
        org, session = await self._prepare_confirm_session(
            db_session,
            org_timezone="UTC",
            allow_same_day_booking=False,
            slot_cutoff_minutes=5,
            booking_date=now_utc.date().isoformat(),
            booking_time=booking_time,
            user_timezone="UTC",
        )

        async def _noop_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] == "REQUIRE_DATE_PICKER"
        assert body["action"]["field_meta"]["policy_reason_code"] == "same_day_disabled"

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is None

    async def test_confirm_booking_cutoff_rejection_returns_reason_and_no_commit(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        self._stub_graph_confirm_path(monkeypatch)
        frozen = datetime.combine(
            datetime.now(timezone.utc).date(),
            time(12, 0),
            tzinfo=timezone.utc,
        )
        self._freeze_clock(monkeypatch, frozen)
        booking_time = (frozen + timedelta(minutes=2)).strftime("%H:%M")
        org, session = await self._prepare_confirm_session(
            db_session,
            org_timezone="UTC",
            allow_same_day_booking=True,
            slot_cutoff_minutes=10,
            booking_date=frozen.date().isoformat(),
            booking_time=booking_time,
            user_timezone="UTC",
        )

        async def _noop_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "confirm", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] == "REQUIRE_TIME_SLOT_GRID"
        assert body["action"]["field_meta"]["policy_reason_code"] == "past_time_cutoff"

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is None

    async def test_confirm_booking_dst_snapshot_and_provider_transport_keep_utc(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        self._stub_graph_confirm_path(monkeypatch)
        org, session = await self._prepare_confirm_session(
            db_session,
            org_timezone="America/New_York",
            allow_same_day_booking=True,
            slot_cutoff_minutes=0,
            booking_date="2030-03-11",
            booking_time="09:00",
            user_timezone="America/Los_Angeles",
        )
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.google_calendar_connected = True
        cfg.google_calendar_refresh_token = "test-refresh-token"
        await db_session.commit()

        async def _noop_emails(*_args, **_kwargs):
            return None

        captured_calendar_call = {}

        async def _capture_calendar_event(*_args, **kwargs):
            captured_calendar_call.update(kwargs)
            return "evt_timezone_dst"

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)
        monkeypatch.setattr(
            "app.core.integrations.google_calendar.create_booking_calendar_event",
            _capture_calendar_event,
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        booking = booking_result.scalars().first()
        assert booking is not None, resp.json()
        assert booking.user_timezone == "America/Los_Angeles"
        assert booking.org_timezone_snapshot == "America/New_York"
        assert booking.timezone_source == "user_selected"
        assert booking.user_slot_start_at.isoformat().startswith("2030-03-11T06:00:00")
        assert booking.org_slot_start_at.isoformat().startswith("2030-03-11T09:00:00")

        slot = await db_session.get(TimeSlot, booking.slot_id)
        slot_start_utc = (
            slot.start_time.replace(tzinfo=timezone.utc)
            if slot.start_time.tzinfo is None
            else slot.start_time.astimezone(timezone.utc)
        )
        slot_end_utc = (
            slot.end_time.replace(tzinfo=timezone.utc)
            if slot.end_time.tzinfo is None
            else slot.end_time.astimezone(timezone.utc)
        )
        assert slot_start_utc.isoformat().startswith("2030-03-11T13:00:00+00:00")
        assert slot_end_utc.isoformat().startswith("2030-03-11T14:00:00+00:00")
        calendar_start_utc = (
            captured_calendar_call["start_time"].replace(tzinfo=timezone.utc)
            if captured_calendar_call["start_time"].tzinfo is None
            else captured_calendar_call["start_time"].astimezone(timezone.utc)
        )
        calendar_end_utc = (
            captured_calendar_call["end_time"].replace(tzinfo=timezone.utc)
            if captured_calendar_call["end_time"].tzinfo is None
            else captured_calendar_call["end_time"].astimezone(timezone.utc)
        )
        assert calendar_start_utc.isoformat().startswith("2030-03-11T13:00:00+00:00")
        assert calendar_end_utc.isoformat().startswith("2030-03-11T14:00:00+00:00")
        assert captured_calendar_call["display_timezone"] == "America/New_York"

    @pytest.mark.parametrize("confirmation_text", ["yes", "confirm", "proceed"])
    async def test_confirm_booking_dst_repeatable_commit_path_never_null(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
        confirmation_text: str,
    ):
        self._stub_graph_confirm_path(monkeypatch)
        async def _noop_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        for _ in range(2):
            org, session = await self._prepare_confirm_session(
                db_session,
                org_timezone="America/New_York",
                allow_same_day_booking=True,
                slot_cutoff_minutes=0,
                booking_date="2030-03-11",
                booking_time="09:00",
                user_timezone="America/Los_Angeles",
            )

            resp = await client.post(
                "/api/v1/chat/message/dispatch",
                json={"message": confirmation_text, "session_id": str(session.id)},
                headers={"X-Org-API-Key": org.api_key},
            )
            assert resp.status_code == 200

            booking_result = await db_session.execute(
                select(Booking).where(Booking.session_id == session.id)
            )
            booking = booking_result.scalars().first()
            assert booking is not None, resp.json()


class TestCancellationCRUD:
    async def test_cancel_booking_by_session_deletes_booking_and_slot(
        self, db_session: AsyncSession
    ):
        from app.crud.booking import cancel_booking_by_session

        org = await _make_org(db_session, "Cancel Org")
        slot = await _make_slot(db_session, org.id, booked=True)
        session = ChatSession(
            org_id=org.id, state_data={}, missing_fields=[], session_status="CONFIRMED"
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        booking = Booking(
            org_id=org.id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Cancel Me",
            customer_email="cancel@test.com",
        )
        db_session.add(booking)
        await db_session.commit()

        cancelled = await cancel_booking_by_session(
            db_session, str(session.id), org_id=str(org.id)
        )
        assert cancelled is True

        booking_check = await db_session.execute(
            select(Booking).where(Booking.id == booking.id)
        )
        assert booking_check.scalars().first() is None
        slot_check = await db_session.execute(
            select(TimeSlot).where(TimeSlot.id == slot.id)
        )
        assert slot_check.scalars().first() is None

    async def test_cancel_booking_by_session_keeps_cross_tenant_booking_untouched(
        self, db_session: AsyncSession
    ):
        from app.crud.booking import cancel_booking_by_session

        org_a = await _make_org(db_session, "Owner Org")
        org_b = await _make_org(db_session, "Other Org")
        slot = await _make_slot(db_session, org_a.id, booked=True)
        session = ChatSession(
            org_id=org_a.id,
            state_data={},
            missing_fields=[],
            session_status="CONFIRMED",
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        booking = Booking(
            org_id=org_a.id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Tenant Safe",
            customer_email="safe@test.com",
        )
        db_session.add(booking)
        await db_session.commit()

        cancelled = await cancel_booking_by_session(
            db_session, str(session.id), org_id=str(org_b.id)
        )
        assert cancelled is False

        still_exists = await db_session.execute(
            select(Booking).where(Booking.id == booking.id)
        )
        assert still_exists.scalars().first() is not None

    async def test_cancel_booking_releases_slot_for_immediate_reuse(
        self, db_session: AsyncSession
    ):
        from app.crud.booking import cancel_booking_by_session

        org = await _make_org(db_session, "Reuse Slot Org")
        slot = await _make_slot(db_session, org.id, booked=True)
        start_time = slot.start_time
        end_time = slot.end_time
        session = ChatSession(
            org_id=org.id, state_data={}, missing_fields=[], session_status="CONFIRMED"
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        booking = Booking(
            org_id=org.id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Reuse Me",
            customer_email="reuse@test.com",
        )
        db_session.add(booking)
        await db_session.commit()

        cancelled = await cancel_booking_by_session(
            db_session, str(session.id), org_id=str(org.id)
        )
        assert cancelled is True

        replacement = TimeSlot(
            org_id=org.id,
            start_time=start_time,
            end_time=end_time,
            is_booked=False,
        )
        db_session.add(replacement)
        await db_session.commit()
        await db_session.refresh(replacement)
        assert replacement.id is not None

    async def test_cancel_booking_lock_conflict_returns_deterministic_error(self):
        from app.crud.booking import cancel_booking

        class _OrigErr(Exception):
            pgcode = "55P03"

        class FakeDB:
            async def execute(self, *_args, **_kwargs):
                raise OperationalError("SELECT ... FOR UPDATE", {}, _OrigErr())

            async def rollback(self):
                return None

            async def commit(self):
                return None

        with pytest.raises(ValueError, match="being modified by another request"):
            await cancel_booking(FakeDB(), session_id=str(uuid.uuid4()))


class TestMeetingProviderModelFields:
    """Test that meeting-provider and meeting-link fields exist on models."""

    async def test_org_config_has_meeting_provider_connection_fields(
        self, db_session: AsyncSession
    ):
        """OrgConfig should have separate meeting provider connected flags."""
        from app.models.config import (
            OrgConfig,
            MeetingProviderDefault,
            ActiveMeetingProvider,
        )

        org = await _make_org(db_session, "Meeting Provider Test Org")

        result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        config = result.scalars().first()
        assert config is not None

        # Meeting provider connection flags should exist and default to False
        assert hasattr(config, "google_meet_connected")
        assert config.google_meet_connected is False
        assert hasattr(config, "microsoft_teams_connected")
        assert config.microsoft_teams_connected is False
        assert hasattr(config, "zoom_connected")
        assert config.zoom_connected is False

        # Active meeting provider should exist and be distinct from active_calendar_provider
        assert hasattr(config, "active_meeting_provider")
        assert config.active_meeting_provider == ActiveMeetingProvider.NONE
        assert hasattr(config, "active_calendar_provider")
        # They are separate fields (not the same)
        assert config.active_meeting_provider is not config.active_calendar_provider

    async def test_org_config_has_zoom_token_fields(self, db_session: AsyncSession):
        """OrgConfig should have Zoom token storage fields."""
        from app.models.config import OrgConfig

        org = await _make_org(db_session, "Zoom Token Test Org")

        result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        config = result.scalars().first()
        assert config is not None

        # Zoom token fields should exist
        assert hasattr(config, "zoom_refresh_token")
        assert hasattr(config, "zoom_access_token")
        assert hasattr(config, "zoom_token_expires_at")
        assert config.zoom_refresh_token is None
        assert config.zoom_access_token is None
        assert config.zoom_token_expires_at is None

    async def test_org_config_has_meeting_provider_audit_fields(
        self, db_session: AsyncSession
    ):
        """OrgConfig should have meeting provider audit fields."""
        from app.models.config import OrgConfig

        org = await _make_org(db_session, "Meeting Provider Audit Test Org")

        result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        config = result.scalars().first()
        assert config is not None

        assert hasattr(config, "meeting_provider_updated_at")
        assert hasattr(config, "meeting_provider_updated_by")
        assert config.meeting_provider_updated_at is None
        assert config.meeting_provider_updated_by is None

    async def test_booking_has_meeting_link_snapshot_fields(
        self, db_session: AsyncSession
    ):
        """Booking should have meeting link snapshot fields."""
        from app.models.booking import Booking
        from app.models.config import MeetingLinkStatus

        org = await _make_org(db_session, "Meeting Link Snapshot Test Org")
        slot = await _make_slot(db_session, org.id)

        session = ChatSession(
            org_id=org.id,
            state_data={},
            missing_fields=[],
            session_status="CONFIRMING",
        )
        db_session.add(session)
        await db_session.commit()

        booking = Booking(
            org_id=org.id,
            slot_id=slot.id,
            session_id=session.id,
            customer_name="Meeting Link Test",
            customer_email="meeting@test.com",
        )
        db_session.add(booking)
        await db_session.commit()
        await db_session.refresh(booking)

        # Meeting link fields should exist
        assert hasattr(booking, "meeting_type_snapshot")
        assert hasattr(booking, "meeting_link_url")
        assert hasattr(booking, "meeting_link_provider")
        assert hasattr(booking, "meeting_link_status")
        assert hasattr(booking, "meeting_link_external_id")
        assert hasattr(booking, "meeting_link_error_code")

        # Default meeting_link_status should be NOT_REQUIRED
        assert booking.meeting_link_status == MeetingLinkStatus.NOT_REQUIRED

    async def test_booking_meeting_link_status_enum_values(
        self, db_session: AsyncSession
    ):
        """MeetingLinkStatus enum should have correct values."""
        from app.models.config import MeetingLinkStatus

        assert MeetingLinkStatus.NOT_REQUIRED.value == "NOT_REQUIRED"
        assert MeetingLinkStatus.READY.value == "READY"
        assert MeetingLinkStatus.UNAVAILABLE.value == "UNAVAILABLE"
        assert MeetingLinkStatus.FAILED.value == "FAILED"

    async def test_active_meeting_provider_enum_values(self, db_session: AsyncSession):
        """ActiveMeetingProvider enum should have correct values."""
        from app.models.config import ActiveMeetingProvider

        assert ActiveMeetingProvider.NONE.value == "NONE"
        assert ActiveMeetingProvider.GOOGLE_MEET.value == "GOOGLE_MEET"
        assert ActiveMeetingProvider.MICROSOFT_TEAMS.value == "MICROSOFT_TEAMS"
        assert ActiveMeetingProvider.ZOOM.value == "ZOOM"
