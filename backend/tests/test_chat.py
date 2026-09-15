"""
Chat endpoint tests — message dispatch, validation, terminal states.
"""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.booking import TimeSlot, Booking, BusinessSchedule
from app.models.system_alert import SystemAlert
from app.crud.otp import generate_otp
from app.engine.message_processor import llm_engine as _global_llm_engine
from app.engine import runtime_router
from app.schemas.agent import ActionPayload

CSRF = {"X-Requested-With": "XMLHttpRequest"}


async def _create_org_with_config(db: AsyncSession) -> Organization:
    """Helper: create a test org + config directly in DB."""
    org = Organization(
        name="Chat Test Org",
        api_key=f"ds_test_{uuid.uuid4().hex[:16]}",
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


async def _create_session(db: AsyncSession, org_id, status="ACTIVE") -> ChatSession:
    """Helper: create a chat session directly in DB."""
    session = ChatSession(
        org_id=org_id,
        state_data={},
        missing_fields=["name", "email", "phone", "date", "time"],
        current_intent="START_BOOKING",
        session_status=status,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


class TestChatDispatch:
    async def test_dispatch_creates_session(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "I want to book an appointment"},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert "action" in body

    async def test_dispatch_with_existing_session(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        session = await _create_session(db_session, org.id)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "My name is John", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == str(session.id)

    async def test_dispatch_sanitizes_structured_json_message_for_stale_widgets(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)

        async def _fake_route_web_message(**_kwargs):
            return ActionPayload(
                message='{"response_text":"Hello! How can I assist you today?","extracted_fields":{},"user_intent":"proceed","confidence":0.2}',
                widget_directive="TEXT_INPUT",
            )

        monkeypatch.setattr(runtime_router, "route_web_message", _fake_route_web_message)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "hello"},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["message"] == "Hello! How can I assist you today?"

    async def test_dispatch_invalid_api_key(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "Hello"},
            headers={"X-Org-API-Key": "ds_invalid_key"},
        )
        assert resp.status_code == 403

    async def test_dispatch_missing_api_key(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "Hello"},
        )
        assert resp.status_code == 401  # Missing required header

    async def test_dispatch_empty_message(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": ""},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 422  # min_length=1 validation

    async def test_dispatch_message_too_long(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "x" * 2001},  # max_length=2000
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 422

    async def test_dispatch_threads_timezone_metadata_into_session_state(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        session = await _create_session(db_session, org.id)
        virtual_slot_id = f"virtual|{org.id}|2030-01-07T14:00:00+00:00"

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={
                "message": "book this slot",
                "session_id": str(session.id),
                "selected_slot_id": virtual_slot_id,
                "user_timezone": "Asia/Kolkata",
                "timezone_source": "user_selected",
                "timezone_fallback_used": False,
            },
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200

        updated = await db_session.get(ChatSession, session.id)
        await db_session.refresh(updated)
        assert updated.state_data.get("selected_slot_id") == virtual_slot_id
        assert updated.state_data.get("user_timezone") == "Asia/Kolkata"
        assert updated.state_data.get("timezone_source") == "user_selected"
        assert updated.state_data.get("timezone_fallback_used") is False

    async def test_otp_verify_legacy_route_alias_accepts_code(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        session = await _create_session(db_session, org.id)
        code = await generate_otp(db_session, str(session.id))

        resp = await client.post(
            "/api/v1/auth/otp/verify",
            json={"session_id": str(session.id), "code": code},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "success"

    async def test_confirming_session_requires_fresh_otp_before_confirmation(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)

        booking_date = datetime.now(timezone.utc) + timedelta(days=2)
        booking_date = booking_date.replace(hour=11, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Resume OTP",
                "email": "resume-otp@test.com",
                "phone": "5551234567",
                "date": booking_date.date().isoformat(),
                "time": "11:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="CONFIRMING",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)
        async def _noop_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={
                "message": "I've verified the code. Please proceed.",
                "session_id": str(session.id),
            },
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] == "REQUIRE_OTP_MODAL"

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        booking = booking_result.scalars().first()
        assert booking is None

        await db_session.refresh(session)
        assert session.session_status in {"ACTIVE", "CONFIRMING"}

    async def test_otp_send_uses_current_email_typed_field_from_session_collected_data(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = True
        cfg.field_definitions = [
            {"name": "name", "type": "text", "label": "Full Name", "required": True},
            {"name": "contact_email", "type": "email", "label": "Email", "required": True},
            {"name": "phone", "type": "phone", "label": "Phone", "required": True},
            {"name": "date", "type": "date", "label": "Date", "required": True},
            {"name": "time", "type": "time", "label": "Time", "required": True},
        ]
        cfg.required_fields = ["name", "contact_email", "phone", "date", "time"]
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=2)
        booking_date = booking_date.replace(hour=10, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Recipient Drift",
                "contact_email": "fresh-contact@test.com",
                "email": "stale-legacy@test.com",
                "phone": "5551234567",
                "date": booking_date.date().isoformat(),
                "time": "10:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=False,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        captured = {}

        async def _capture_otp_email(to_email, *_args, **_kwargs):
            captured["to_email"] = to_email
            return None

        monkeypatch.setattr("app.core.mail.send_otp_email", _capture_otp_email)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes confirm", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["action"]["widget_directive"] == "REQUIRE_OTP_MODAL"
        assert captured["to_email"] == "fresh-contact@test.com"

    async def test_confirmation_email_uses_current_email_typed_field_from_session_collected_data(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.field_definitions = [
            {"name": "name", "type": "text", "label": "Full Name", "required": True},
            {"name": "contact_email", "type": "email", "label": "Email", "required": True},
            {"name": "phone", "type": "phone", "label": "Phone", "required": True},
            {"name": "date", "type": "date", "label": "Date", "required": True},
            {"name": "time", "type": "time", "label": "Time", "required": True},
        ]
        cfg.required_fields = ["name", "contact_email", "phone", "date", "time"]
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=2)
        booking_date = booking_date.replace(hour=12, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Recipient Drift",
                "contact_email": "fresh-confirm@test.com",
                "email": "stale-confirm@test.com",
                "phone": "5551234567",
                "date": booking_date.date().isoformat(),
                "time": "12:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=False,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        captured = {}

        async def _capture_confirmation_email(customer_email, *_args, **_kwargs):
            captured["customer_email"] = customer_email
            return None

        monkeypatch.setattr(
            "app.core.mail.send_confirmation_emails", _capture_confirmation_email
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes confirm", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["action"]["widget_directive"] == "BOOKING_CONFIRMED_CARD"
        assert captured["customer_email"] == "fresh-confirm@test.com"

    async def test_llm_outage_still_collects_time_and_advances_to_next_required_field(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        session = await _create_session(db_session, org.id)

        # Pre-seed collected fields so only time/email/phone remain.
        session.state_data = {
            "name": "Fallback User",
            "date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
        }
        session.missing_fields = ["time", "email", "phone"]
        session.current_intent = "PROVIDE_DATA"
        await db_session.commit()

        async def _llm_returns_none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_global_llm_engine, "_call_llm", _llm_returns_none)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "1:30 PM", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] == "REQUIRE_EMAIL_INPUT"
        assert "temporary issue" in body["action"]["message"].lower()
        assert "email" in body["action"]["message"].lower()

        await db_session.refresh(session)
        assert session.state_data.get("time") == "13:30:00"
        assert session.missing_fields == ["email", "phone"]

    async def test_llm_outage_time_slot_step_keeps_grid_and_message_actionable(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        session = await _create_session(db_session, org.id)

        session.state_data = {
            "name": "Fallback User",
            "date": (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),
            "email": "fallback@example.com",
            "phone": "5551234567",
        }
        session.missing_fields = ["time"]
        session.current_intent = "PROVIDE_DATA"
        await db_session.commit()

        async def _llm_returns_none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_global_llm_engine, "_call_llm", _llm_returns_none)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "hello", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] in {
            "REQUIRE_TIME_SLOT_GRID",
            "TEXT_INPUT",
        }
        assert (
            "time" in body["action"]["message"].lower()
            or "name" in body["action"]["message"].lower()
        )
        assert "temporary issue" in body["action"]["message"].lower()
        assert "time" in body["action"]["message"].lower()

    async def test_llm_outage_prioritizes_time_before_other_missing_fields(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()

        # Reorder required fields to mirror real-world misconfigured tenant ordering.
        cfg.required_fields = ["name", "date", "email", "gender", "phone", "time"]
        cfg.field_definitions = [
            {
                "name": "name",
                "type": "text",
                "label": "Full Name",
                "required": True,
                "order": 0,
            },
            {
                "name": "date",
                "type": "date",
                "label": "Preferred Date",
                "required": True,
                "order": 1,
            },
            {
                "name": "email",
                "type": "email",
                "label": "Email Address",
                "required": True,
                "order": 2,
            },
            {
                "name": "gender",
                "type": "select",
                "label": "Gender",
                "required": True,
                "order": 3,
                "options": ["Male", "Female", "Others"],
            },
            {
                "name": "phone",
                "type": "phone",
                "label": "Phone Number",
                "required": True,
                "order": 4,
            },
            {
                "name": "time",
                "type": "time",
                "label": "Preferred Time",
                "required": True,
                "order": 5,
            },
        ]
        await db_session.commit()

        session = await _create_session(db_session, org.id)
        session.state_data = {
            "name": "Fallback User",
            "date": (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),
            "email": "fallback@example.com",
            "gender": "Male",
            "phone": "5551234567",
        }
        session.missing_fields = ["time"]
        session.current_intent = "PROVIDE_DATA"
        await db_session.commit()

        async def _llm_returns_none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_global_llm_engine, "_call_llm", _llm_returns_none)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "hi", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] in {
            "REQUIRE_TIME_SLOT_GRID",
            "TEXT_INPUT",
        }
        assert (
            "time" in body["action"]["message"].lower()
            or "name" in body["action"]["message"].lower()
        )

    async def test_llm_outage_greeting_does_not_backfill_midnight_time(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.required_fields = ["name", "date", "email", "gender", "phone", "time"]
        cfg.field_definitions = [
            {
                "name": "name",
                "type": "text",
                "label": "Full Name",
                "required": True,
                "order": 0,
            },
            {
                "name": "date",
                "type": "date",
                "label": "Preferred Date",
                "required": True,
                "order": 1,
            },
            {
                "name": "email",
                "type": "email",
                "label": "Email",
                "required": True,
                "order": 2,
            },
            {
                "name": "gender",
                "type": "select",
                "label": "Gender",
                "required": True,
                "order": 3,
                "options": ["Male", "Female", "Others"],
            },
            {
                "name": "phone",
                "type": "phone",
                "label": "Phone",
                "required": True,
                "order": 4,
            },
            {
                "name": "time",
                "type": "time",
                "label": "Preferred Time",
                "required": True,
                "order": 5,
            },
        ]
        await db_session.commit()

        session = await _create_session(db_session, org.id)
        session.state_data = {
            "date": (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),
            "email": "fallback@example.com",
            "gender": "Male",
            "phone": "5551234567",
        }
        session.missing_fields = ["name", "time"]
        session.current_intent = "START_BOOKING"
        await db_session.commit()

        async def _llm_returns_none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_global_llm_engine, "_call_llm", _llm_returns_none)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "HI", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] in {
            "REQUIRE_TIME_SLOT_GRID",
            "TEXT_INPUT",
        }
        assert (
            "time" in body["action"]["message"].lower()
            or "name" in body["action"]["message"].lower()
        )

        await db_session.refresh(session)
        assert session.state_data.get("time") is None

    async def test_llm_outage_greeting_does_not_backfill_name(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        session = await _create_session(db_session, org.id)
        session.state_data = {
            "date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
            "email": "fallback@example.com",
            "phone": "5551234567",
        }
        session.missing_fields = ["name", "time"]
        session.current_intent = "START_BOOKING"
        await db_session.commit()

        async def _llm_returns_none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_global_llm_engine, "_call_llm", _llm_returns_none)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "HI", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] in {
            "REQUIRE_TIME_SLOT_GRID",
            "TEXT_INPUT",
        }
        assert (
            "time" in body["action"]["message"].lower()
            or "name" in body["action"]["message"].lower()
        )

        await db_session.refresh(session)
        assert session.state_data.get("name") is None

    async def test_llm_outage_date_like_text_does_not_backfill_time_as_midnight(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.required_fields = ["name", "date", "email", "gender", "phone", "time"]
        cfg.field_definitions = [
            {
                "name": "name",
                "type": "text",
                "label": "Full Name",
                "required": True,
                "order": 0,
            },
            {
                "name": "date",
                "type": "date",
                "label": "Preferred Date",
                "required": True,
                "order": 1,
            },
            {
                "name": "email",
                "type": "email",
                "label": "Email",
                "required": True,
                "order": 2,
            },
            {
                "name": "gender",
                "type": "select",
                "label": "Gender",
                "required": True,
                "order": 3,
                "options": ["Male", "Female", "Others"],
            },
            {
                "name": "phone",
                "type": "phone",
                "label": "Phone",
                "required": True,
                "order": 4,
            },
            {
                "name": "time",
                "type": "time",
                "label": "Preferred Time",
                "required": True,
                "order": 5,
            },
        ]
        await db_session.commit()

        session = await _create_session(db_session, org.id)
        session.state_data = {
            "name": "Fallback User",
            "date": (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),
            "email": "fallback@example.com",
            "gender": "Male",
            "phone": "5551234567",
        }
        session.missing_fields = ["time"]
        session.current_intent = "PROVIDE_DATA"
        await db_session.commit()

        async def _llm_returns_none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(_global_llm_engine, "_call_llm", _llm_returns_none)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "2026-04-01", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"]["widget_directive"] == "REQUIRE_TIME_SLOT_GRID"
        assert "temporary issue" in body["action"]["message"].lower()
        assert "time" in body["action"]["message"].lower()

        await db_session.refresh(session)
        assert session.state_data.get("time") is None
        assert "time" in session.missing_fields

    async def test_confirm_booking_with_timezone_fallback_adds_notice_and_persists_source(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.timezone = "UTC"
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=2)
        booking_date = booking_date.replace(hour=15, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "TZ Fallback User",
                "email": "tz-fallback@test.com",
                "phone": "5551231234",
                "date": booking_date.date().isoformat(),
                "time": "15:00",
                "user_timezone": "Mars/Phobos",
                "timezone_source": "user_selected",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=False,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        async def _noop_confirmation_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr(
            "app.core.mail.send_confirmation_emails", _noop_confirmation_emails
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "used the business timezone" in body["action"]["message"].lower()
        assert "confirmation email is being sent now" in body["action"]["message"].lower()

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is not None


class TestMeetingLinkGeneration:
    """Tests for meeting link generation during ONLINE_ONLY booking flow."""

    async def test_online_only_booking_persists_user_and_org_slot_in_business_timezone(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Slot chosen in business timezone should persist matching local snapshots."""
        from app.models.config import ActiveMeetingProvider
        from app.core.integrations.meeting_providers import MeetingLinkResult

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.allow_hybrid_selection = False
        cfg.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        cfg.google_meet_connected = True
        cfg.timezone = "Asia/Kolkata"

        schedule_result = await db_session.execute(
            select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
        )
        schedule = schedule_result.scalars().first()
        if schedule:
            schedule.timezone = "Asia/Kolkata"
            schedule.slot_duration_minutes = 30
        else:
            db_session.add(
                BusinessSchedule(
                    org_id=org.id,
                    slot_duration_minutes=30,
                    booking_window_days=60,
                    timezone="Asia/Kolkata",
                )
            )
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "TZ Snapshot",
                "email": "tz-snapshot@test.com",
                "phone": "5551234567",
                "date": "2030-01-07",
                "time": "9:00 AM",
                "user_timezone": "Asia/Kolkata",
                "timezone_source": "user_selected",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        async def mock_generate_link_success(*args, **kwargs):
            return MeetingLinkResult(
                status="READY",
                meeting_link_url="https://meet.google.com/tz-snapshot-link",
                external_id="tz-snapshot-1",
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link_success,
        )

        async def _noop_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        async def _noop_calendar_event(*_args, **_kwargs):
            return None

        monkeypatch.setattr(
            "app.core.integrations.google_calendar.create_booking_calendar_event",
            _noop_calendar_event,
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
        assert booking is not None

        # 09:00 Asia/Kolkata should persist as 09:00 local snapshots.
        assert booking.user_timezone == "Asia/Kolkata"
        assert booking.org_timezone_snapshot == "Asia/Kolkata"
        assert booking.user_slot_start_at is not None
        assert booking.org_slot_start_at is not None
        assert booking.user_slot_start_at.hour == 9
        assert booking.user_slot_start_at.minute == 0
        assert booking.org_slot_start_at.hour == 9
        assert booking.org_slot_start_at.minute == 0

    async def test_google_calendar_event_uses_timezone_aware_slot_values(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Google event creation should receive UTC slot matching business-local choice."""
        from app.models.config import ActiveMeetingProvider
        from app.core.integrations.meeting_providers import MeetingLinkResult

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.allow_hybrid_selection = False
        cfg.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        cfg.google_meet_connected = True
        cfg.google_calendar_connected = True
        cfg.google_calendar_refresh_token = "dummy-refresh-token"
        cfg.timezone = "Asia/Kolkata"

        schedule_result = await db_session.execute(
            select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
        )
        schedule = schedule_result.scalars().first()
        if schedule:
            schedule.timezone = "Asia/Kolkata"
            schedule.slot_duration_minutes = 30
        else:
            db_session.add(
                BusinessSchedule(
                    org_id=org.id,
                    slot_duration_minutes=30,
                    booking_window_days=60,
                    timezone="Asia/Kolkata",
                )
            )
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Calendar TZ",
                "email": "calendar-tz@test.com",
                "phone": "5559990000",
                "date": "2030-01-07",
                "time": "12:30 PM",
                "user_timezone": "Asia/Kolkata",
                "timezone_source": "user_selected",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        async def mock_generate_link_success(*args, **kwargs):
            return MeetingLinkResult(
                status="READY",
                meeting_link_url="https://meet.google.com/calendar-tz-link",
                external_id="calendar-tz-1",
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link_success,
        )

        captured_event_args = {}

        async def _capture_calendar_event(**kwargs):
            captured_event_args.update(kwargs)
            return "fake-google-event-id"

        monkeypatch.setattr(
            "app.core.integrations.google_calendar.create_booking_calendar_event",
            _capture_calendar_event,
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

        # 12:30 Asia/Kolkata should map to 07:00 UTC for Google event payload.
        start_time = captured_event_args.get("start_time")
        end_time = captured_event_args.get("end_time")
        assert start_time is not None
        assert end_time is not None
        assert start_time.hour == 7 and start_time.minute == 0
        assert end_time.hour == 7 and end_time.minute == 30
        assert captured_event_args.get("display_timezone") == "Asia/Kolkata"

    async def test_online_only_provider_success_sets_meeting_link_ready(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """ONLINE_ONLY with active provider success -> READY + non-empty link."""
        from app.models.config import ActiveMeetingProvider, MeetingLinkStatus
        from app.core.integrations.meeting_providers import MeetingLinkResult

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.allow_hybrid_selection = False
        cfg.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        cfg.google_meet_connected = True
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=2)
        booking_date = booking_date.replace(hour=14, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Online Link Test",
                "email": "online-link@test.com",
                "phone": "5551234567",
                "date": booking_date.date().isoformat(),
                "time": "14:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Mock provider to return success
        async def mock_generate_link(*args, **kwargs):
            return MeetingLinkResult(
                status="READY",
                meeting_link_url="https://meet.google.com/abc-defg-hij",
                external_id="meet-abc123",
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link,
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

        # Verify booking has meeting link persisted
        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        booking = booking_result.scalars().first()
        assert booking is not None
        assert booking.meeting_link_status == MeetingLinkStatus.READY
        assert booking.meeting_link_url == "https://meet.google.com/abc-defg-hij"
        assert booking.meeting_link_provider == ActiveMeetingProvider.GOOGLE_MEET
        assert booking.meeting_type_snapshot == "ONLINE"

    async def test_online_only_provider_failure_confirms_booking_with_unavailable_status(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """ONLINE_ONLY + provider failure -> booking confirmed + UNAVAILABLE + degraded response."""
        from app.models.config import ActiveMeetingProvider, MeetingLinkStatus
        from app.core.integrations.meeting_providers import (
            MeetingLinkResult,
            MeetingLinkErrorCode,
        )

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.allow_hybrid_selection = False
        cfg.active_meeting_provider = ActiveMeetingProvider.ZOOM
        cfg.zoom_connected = True
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=10, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Degraded Link Test",
                "email": "degraded-link@test.com",
                "phone": "5559876543",
                "date": booking_date.date().isoformat(),
                "time": "10:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Mock provider to return failure
        async def mock_generate_link_failure(*args, **kwargs):
            return MeetingLinkResult(
                status="UNAVAILABLE",
                meeting_link_url=None,
                error_code=MeetingLinkErrorCode.TIMEOUT,
                error_message="Provider API timeout",
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link_failure,
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

        # Booking should still be confirmed (D-01)
        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        booking = booking_result.scalars().first()
        assert booking is not None, "Booking should be created even on link failure"

        # Meeting link status should be UNAVAILABLE (D-02)
        assert booking.meeting_link_status == MeetingLinkStatus.UNAVAILABLE
        assert booking.meeting_link_url is None
        assert booking.meeting_link_error_code == "TIMEOUT"
        assert booking.meeting_type_snapshot == "ONLINE"

        # Response should provide explicit degraded guidance (D-03)
        degraded_message = body["action"]["message"].lower()
        assert "couldn't be generated" in degraded_message
        assert "reservation is saved" in degraded_message
        assert "send it to you shortly" not in degraded_message

        alerts_result = await db_session.execute(
            select(SystemAlert)
            .where(SystemAlert.org_id == org.id)
            .where(SystemAlert.code == "ONLINE_ONLY_MEETING_LINK_UNAVAILABLE")
        )
        alert = alerts_result.scalars().first()
        assert alert is not None
        assert alert.component == "booking"
        assert (alert.context or {}).get("provider") == "ZOOM"
        assert (alert.context or {}).get("meeting_link_status") == "UNAVAILABLE"
        assert (alert.context or {}).get("error_code") == "TIMEOUT"
        assert "email" not in (alert.context or {})
        assert "phone" not in (alert.context or {})
        assert "name" not in (alert.context or {})
        assert "token" not in (alert.context or {})
        assert "http" not in degraded_message
        assert "join" not in degraded_message

    async def test_hybrid_mode_no_meeting_link_generation_returns_not_required(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """HYBRID confirmation path -> no meeting-link generation call, status NOT_REQUIRED."""
        from app.models.config import ActiveMeetingProvider, MeetingLinkStatus

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "HYBRID"
        cfg.allow_hybrid_selection = True
        cfg.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        cfg.google_meet_connected = True
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=4)
        booking_date = booking_date.replace(hour=11, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Hybrid Test",
                "email": "hybrid@test.com",
                "phone": "5551112233",
                "date": booking_date.date().isoformat(),
                "time": "11:00",
                "meeting_type": "OFFLINE",  # HYBRID allows OFFLINE choice
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Track if link generation was called (it should NOT be)
        link_generation_called = []

        async def mock_generate_link_should_not_be_called(*args, **kwargs):
            link_generation_called.append(True)
            raise AssertionError(
                "Link generation should not be called for HYBRID/OFFLINE"
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link_should_not_be_called,
        )

        async def _noop_emails(*_args, **_kwargs):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes please", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200

        # Verify link generation was NOT called (D-09, D-10)
        assert len(link_generation_called) == 0, (
            "Link generation should not be called for HYBRID mode"
        )

        # Verify booking has NOT_REQUIRED status
        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        booking = booking_result.scalars().first()
        assert booking is not None
        assert booking.meeting_link_status == MeetingLinkStatus.NOT_REQUIRED
        assert booking.meeting_link_url is None
        assert booking.meeting_type_snapshot == "OFFLINE"

    async def test_confirm_time_policy_change_revalidates_against_latest_mode(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "HYBRID"
        cfg.allow_hybrid_selection = True
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=11, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Late Policy",
                "email": "late@test.com",
                "phone": "5559991234",
                "date": booking_date.date().isoformat(),
                "time": "11:00",
                "meeting_type": "ONLINE",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        cfg.meeting_mode = "OFFLINE_ONLY"
        await db_session.commit()

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "confirm booking", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "allows offline meetings only" in body["action"]["message"].lower()

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is None

    async def test_confirm_time_policy_revalidation_rejects_offline_in_online_only_mode(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "OFFLINE_ONLY"
        cfg.allow_hybrid_selection = False
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=9, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Mode Revalidation",
                "email": "mode-revalidation@test.com",
                "phone": "5551239876",
                "date": booking_date.date().isoformat(),
                "time": "09:00",
                "meeting_type": "OFFLINE",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        cfg.meeting_mode = "ONLINE_ONLY"
        await db_session.commit()

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "confirm booking", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "allows online meetings only" in body["action"]["message"].lower()

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is None

    async def test_confirm_time_policy_revalidation_rejects_online_when_hybrid_selection_disabled(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "HYBRID"
        cfg.allow_hybrid_selection = True
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=5)
        booking_date = booking_date.replace(hour=13, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Hybrid Gate",
                "email": "hybrid-gate@test.com",
                "phone": "5553214567",
                "date": booking_date.date().isoformat(),
                "time": "13:00",
                "meeting_type": "ONLINE",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        cfg.allow_hybrid_selection = False
        await db_session.commit()

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "confirm booking", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "allows offline meetings only" in body["action"]["message"].lower()

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is None

    async def test_confirm_time_online_degradation_returns_actionable_guidance(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.allow_hybrid_selection = False
        cfg.meeting_provider_default = "NONE"
        cfg.google_calendar_connected = False
        cfg.outlook_calendar_connected = False
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=4)
        booking_date = booking_date.replace(hour=12, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Online Path",
                "email": "online@test.com",
                "phone": "5551112222",
                "date": booking_date.date().isoformat(),
                "time": "12:00",
                "meeting_type": "ONLINE",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes please", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "can't finalize an online meeting" in body["action"]["message"].lower()
        assert "choose an offline meeting option" in body["action"]["message"].lower()

        booking_result = await db_session.execute(
            select(Booking).where(Booking.session_id == session.id)
        )
        assert booking_result.scalars().first() is None

    async def test_cancel_confirmed_booking_email_payload_includes_meeting_snapshot_fields(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Chat cancellation sends meeting snapshot fields in cancellation payload."""
        from app.models.config import ActiveMeetingProvider, MeetingLinkStatus

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        cfg.google_meet_connected = True
        cfg.timezone = "UTC"
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=16, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=True,
        )
        db_session.add(slot)
        await db_session.commit()
        await db_session.refresh(slot)

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Cancel Payload User",
                "email": "cancel-payload@test.com",
                "phone": "5557778888",
                "date": booking_date.date().isoformat(),
                "time": "16:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="CONFIRMED",
            otp_verified=True,
            channel="web",
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        booking = Booking(
            org_id=org.id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Cancel Payload User",
            customer_email="cancel-payload@test.com",
            meeting_type_snapshot="ONLINE",
            meeting_link_status=MeetingLinkStatus.READY,
            meeting_link_url="https://meet.example.com/chat-cancel",
            meeting_link_provider=ActiveMeetingProvider.GOOGLE_MEET,
            user_timezone="Asia/Kolkata",
            org_timezone_snapshot="UTC",
        )
        db_session.add(booking)
        await db_session.commit()

        captured_payload = {}

        async def _capture_send_cancellation_email(
            customer_email, customer_name, booking_details, **kwargs
        ):
            captured_payload["customer_email"] = customer_email
            captured_payload["customer_name"] = customer_name
            captured_payload["booking_details"] = booking_details
            captured_payload["kwargs"] = kwargs

        monkeypatch.setattr(
            "app.core.mail.send_cancellation_email", _capture_send_cancellation_email
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "cancel my booking", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        assert "are you sure" in resp.json()["action"]["message"].lower()

        resp2 = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp2.status_code == 200

        payload = captured_payload.get("booking_details", {})
        assert payload.get("meeting_type_snapshot") == "ONLINE"
        assert payload.get("meeting_link_status") == "READY"
        assert payload.get("meeting_link_url") == "https://meet.example.com/chat-cancel"
        assert payload.get("meeting_link_provider") == "GOOGLE_MEET"


class TestConfirmationEmailMeetingLink:
    """Test meeting_link confirmation email content rendering."""

    async def test_confirmation_email_meeting_link_ready_includes_link_cta(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """READY status -> confirmation email includes clickable meeting link block."""
        from app.models.config import ActiveMeetingProvider, MeetingLinkStatus
        from app.core.integrations.meeting_providers import (
            MeetingLinkResult,
        )

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        cfg.google_meet_connected = True
        cfg.timezone = "America/New_York"
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=14, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Ready Link Test",
                "email": "ready-link@test.com",
                "phone": "5551234567",
                "date": booking_date.date().isoformat(),
                "time": "14:00",
                "user_timezone": "Europe/London",
                "timezone_source": "user_selected",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Mock provider to return successful link
        async def mock_generate_link_success(*args, **kwargs):
            return MeetingLinkResult(
                status="READY",
                meeting_link_url="https://meet.google.com/test-ready-link",
                external_id="test-meeting-123",
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link_success,
        )

        # Capture the email payload
        captured_email_payload = {}

        async def mock_capture_emails(
            customer_email, admin_email, booking_details, **kwargs
        ):
            captured_email_payload["booking_details"] = booking_details
            return None

        monkeypatch.setattr(
            "app.core.mail.send_confirmation_emails", mock_capture_emails
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200

        # Verify email payload includes meeting link fields for READY status
        bd = captured_email_payload.get("booking_details", {})
        assert bd.get("meeting_link_status") == "READY", (
            "Email should include meeting_link_status"
        )
        assert (
            bd.get("meeting_link_url") == "https://meet.google.com/test-ready-link"
        ), "Email should include meeting_link_url"
        assert bd.get("meeting_link_provider") == "GOOGLE_MEET", (
            "Email should include provider"
        )
        assert bd.get("meeting_type_snapshot") == "ONLINE", (
            "Email should include meeting type snapshot"
        )

    async def test_confirmation_email_meeting_link_unavailable_includes_guidance(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """UNAVAILABLE status -> email includes explicit unavailability notice and guidance text."""
        from app.models.config import ActiveMeetingProvider, MeetingLinkStatus
        from app.core.integrations.meeting_providers import (
            MeetingLinkResult,
            MeetingLinkErrorCode,
        )

        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "ONLINE_ONLY"
        cfg.active_meeting_provider = ActiveMeetingProvider.ZOOM
        cfg.zoom_connected = True
        cfg.timezone = "UTC"
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=15, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Unavailable Link Test",
                "email": "unavailable-link@test.com",
                "phone": "5557654321",
                "date": booking_date.date().isoformat(),
                "time": "15:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Mock provider to return failure
        async def mock_generate_link_failure(*args, **kwargs):
            return MeetingLinkResult(
                status="UNAVAILABLE",
                meeting_link_url=None,
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message="Provider service error",
            )

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.factory.generate_meeting_link",
            mock_generate_link_failure,
        )

        # Capture the email payload
        captured_email_payload = {}

        async def mock_capture_emails(
            customer_email, admin_email, booking_details, **kwargs
        ):
            captured_email_payload["booking_details"] = booking_details
            return None

        monkeypatch.setattr(
            "app.core.mail.send_confirmation_emails", mock_capture_emails
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes confirm", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200

        # Verify email payload includes UNAVAILABLE status with no fake link
        bd = captured_email_payload.get("booking_details", {})
        assert bd.get("meeting_link_status") == "UNAVAILABLE", (
            "Email should include UNAVAILABLE status"
        )
        assert bd.get("meeting_link_url") is None, (
            "Email should NOT include link URL for UNAVAILABLE"
        )
        assert bd.get("meeting_type_snapshot") == "ONLINE", (
            "Email should include meeting type snapshot"
        )

    async def test_confirmation_email_includes_dual_time_context(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch
    ):
        """Confirmation email includes user-facing and org-canonical time snapshots."""
        org = await _create_org_with_config(db_session)
        cfg_result = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        cfg = cfg_result.scalars().first()
        cfg.require_otp = False
        cfg.meeting_mode = "OFFLINE_ONLY"
        cfg.timezone = "America/New_York"
        await db_session.commit()

        booking_date = datetime.now(timezone.utc) + timedelta(days=3)
        booking_date = booking_date.replace(hour=18, minute=0, second=0, microsecond=0)
        slot = TimeSlot(
            org_id=org.id,
            start_time=booking_date,
            end_time=booking_date + timedelta(hours=1),
            is_booked=False,
        )
        db_session.add(slot)
        await db_session.commit()

        session = ChatSession(
            org_id=org.id,
            state_data={
                "name": "Dual Time Test",
                "email": "dual-time@test.com",
                "phone": "5551112222",
                "date": booking_date.date().isoformat(),
                "time": "18:00",
                "user_timezone": "Asia/Tokyo",
                "timezone_source": "detected",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=True,
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Capture the email payload
        captured_email_payload = {}

        async def mock_capture_emails(
            customer_email, admin_email, booking_details, **kwargs
        ):
            captured_email_payload["booking_details"] = booking_details
            return None

        monkeypatch.setattr(
            "app.core.mail.send_confirmation_emails", mock_capture_emails
        )

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(session.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200

        # Verify email payload includes dual-time context
        bd = captured_email_payload.get("booking_details", {})
        assert "user_timezone" in bd, "Email should include user timezone"
        assert "org_timezone_snapshot" in bd, (
            "Email should include org timezone snapshot"
        )
        assert "user_slot_start_at" in bd or "user_slot_start_at_formatted" in bd, (
            "Email should include user slot start"
        )
        assert "org_slot_start_at" in bd or "org_slot_start_at_formatted" in bd, (
            "Email should include org slot start"
        )


@pytest.mark.asyncio
async def test_confirmation_email_and_meeting_link_ready_renders_join_cta_and_dual_time(
    monkeypatch,
):
    """Online Meeting Link Delivery: READY renders link CTA and dual-time block."""
    from app.core import mail

    captured: list[dict] = []

    async def _capture_send_email_with_failover(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        mail, "send_email_with_failover", _capture_send_email_with_failover
    )

    booking_details = {
        "name": "Alice",
        "email": "alice@example.com",
        "phone": "12345",
        "date": "2030-01-07",
        "time": "10:00",
        "source_channel": "Web Widget",
        "meeting_type_snapshot": "ONLINE",
        "meeting_link_status": "READY",
        "meeting_link_url": "https://meet.example.com/abc",
        "meeting_link_provider": "GOOGLE_MEET",
        "user_timezone": "Asia/Kolkata",
        "user_slot_start_at": "2030-01-07T15:30:00+05:30",
        "user_slot_end_at": "2030-01-07T16:30:00+05:30",
        "org_timezone_snapshot": "America/New_York",
        "org_slot_start_at": "2030-01-07T05:00:00-05:00",
        "org_slot_end_at": "2030-01-07T06:00:00-05:00",
    }

    await mail.send_confirmation_emails(
        customer_email="alice@example.com",
        admin_email="admin@example.com",
        booking_details=booking_details,
        org_id=uuid.uuid4(),
    )

    assert len(captured) == 2
    customer_body = captured[0]["body_html"]
    admin_body = captured[1]["body_html"]

    assert "Meeting link status" in customer_body
    assert "status-badge--ready" in customer_body or "READY" in customer_body.upper()
    assert 'href="https://meet.example.com/abc"' in customer_body
    assert "Join Meeting" in customer_body
    assert "Time Context" in customer_body
    assert "User time (Asia/Kolkata)" in customer_body
    assert "Business time (America/New_York)" in customer_body

    assert "Meeting link status" in admin_body
    assert "status-badge--ready" in admin_body or "READY" in admin_body.upper()
    assert 'href="https://meet.example.com/abc"' in admin_body


@pytest.mark.asyncio
async def test_confirmation_email_and_meeting_link_unavailable_renders_guidance_without_url(
    monkeypatch,
):
    """Online Meeting Link Delivery: UNAVAILABLE renders explicit guidance and no URL."""
    from app.core import mail

    captured: list[dict] = []

    async def _capture_send_email_with_failover(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        mail, "send_email_with_failover", _capture_send_email_with_failover
    )

    booking_details = {
        "name": "Bob",
        "email": "bob@example.com",
        "date": "2030-01-08",
        "time": "11:00",
        "meeting_type_snapshot": "ONLINE",
        "meeting_link_status": "UNAVAILABLE",
        "meeting_link_provider": "ZOOM",
    }

    await mail.send_confirmation_emails(
        customer_email="bob@example.com",
        admin_email="admin@example.com",
        booking_details=booking_details,
        org_id=uuid.uuid4(),
    )

    assert len(captured) == 2
    customer_body = captured[0]["body_html"]
    admin_body = captured[1]["body_html"]

    assert "Meeting link status" in customer_body
    assert "status-badge--unavailable" in customer_body or "UNAVAILABLE" in customer_body.upper()
    assert "temporarily unavailable" in customer_body
    assert "retry shortly or contact support" in customer_body
    assert "Join Meeting" not in customer_body
    assert 'href="http' not in customer_body

    assert "Meeting link status" in admin_body
    assert "status-badge--unavailable" in admin_body or "UNAVAILABLE" in admin_body.upper()
    assert "follow up with the customer" in admin_body
    assert "Join Meeting" not in admin_body


@pytest.mark.asyncio
async def test_cancellation_email_and_meeting_link_ready_renders_join_cta(monkeypatch):
    """Cancellation + meeting_link: READY renders status + Join Meeting CTA."""
    from app.core import mail

    captured: list[dict] = []

    async def _capture_send_email_with_failover(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        mail, "send_email_with_failover", _capture_send_email_with_failover
    )

    booking_details = {
        "date": "2030-01-09",
        "time": "12:00",
        "meeting_type_snapshot": "ONLINE",
        "meeting_link_status": "READY",
        "meeting_link_url": "https://meet.example.com/cancel-ready",
        "meeting_link_provider": "GOOGLE_MEET",
    }

    await mail.send_cancellation_email(
        customer_email="ready-cancel@example.com",
        customer_name="Ready Cancel",
        booking_details=booking_details,
        org_id=uuid.uuid4(),
    )

    assert len(captured) == 1
    body = captured[0]["body_html"]
    assert "Meeting link status" in body
    assert "status-badge--ready" in body or "READY" in body.upper()
    assert "Join Meeting" in body
    assert 'href="https://meet.example.com/cancel-ready"' in body


@pytest.mark.asyncio
async def test_cancellation_email_and_meeting_link_unavailable_renders_guidance_without_url(
    monkeypatch,
):
    """Cancellation + meeting_link: UNAVAILABLE renders explicit guidance and no URL."""
    from app.core import mail

    captured: list[dict] = []

    async def _capture_send_email_with_failover(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        mail, "send_email_with_failover", _capture_send_email_with_failover
    )

    booking_details = {
        "date": "2030-01-10",
        "time": "13:00",
        "meeting_type_snapshot": "ONLINE",
        "meeting_link_status": "UNAVAILABLE",
        "meeting_link_provider": "ZOOM",
    }

    await mail.send_cancellation_email(
        customer_email="unavailable-cancel@example.com",
        customer_name="Unavailable Cancel",
        booking_details=booking_details,
        org_id=uuid.uuid4(),
    )

    assert len(captured) == 1
    body = captured[0]["body_html"]
    assert "Meeting link status" in body
    assert "status-badge--unavailable" in body or "UNAVAILABLE" in body.upper()
    assert "temporarily unavailable" in body
    assert "retry shortly or contact support" in body
    assert "Join Meeting" not in body
    assert 'href="http' not in body


@pytest.mark.asyncio
async def test_google_calendar_description_formats_time_in_display_timezone(
    monkeypatch,
):
    from app.core.integrations import google_calendar

    captured_payload = {}

    async def _mock_refresh(_refresh_token):
        return {"access_token": "fake-access-token"}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "fake-event-id"}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured_payload["url"] = url
            captured_payload["headers"] = headers
            captured_payload["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(google_calendar, "refresh_access_token", _mock_refresh)
    monkeypatch.setattr(
        google_calendar.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient(),
    )

    start_utc = datetime(2030, 1, 7, 7, 0, tzinfo=timezone.utc)
    end_utc = datetime(2030, 1, 7, 7, 30, tzinfo=timezone.utc)

    event_id = await google_calendar.create_booking_calendar_event(
        org_id="org-1",
        refresh_token="refresh-token",
        customer_name="Calendar User",
        customer_email="calendar-user@test.com",
        start_time=start_utc,
        end_time=end_utc,
        booking_id="booking-1",
        customer_phone="5550001111",
        source_channel="Web Widget",
        service_type="Appointment",
        org_name="DotStark",
        display_timezone="Asia/Kolkata",
    )

    assert event_id == "fake-event-id"
    payload = captured_payload.get("json", {})
    description = payload.get("description", "")

    assert "Date:    Monday, January 07, 2030" in description
    assert "Time:    12:30 PM - 01:00 PM (Asia/Kolkata)" in description
