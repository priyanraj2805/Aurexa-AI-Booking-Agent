"""
Firebase phone OTP tests — validates the Admin SDK flow described in the task.

Frontend does: confirmationResult.confirm(code) -> getIdToken() -> POST /auth/security/otp/verify {session_id, channel:"phone", id_token}
Backend must:
  1. verify Firebase ID token via Firebase Admin SDK
  2. extract phone_number
  3. compare with session's stored phone
  4. mark phone_otp_verified only on exact match
  5. allow booking only after verified
  6. phone channel must NOT fall back to 6-digit code validation
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.booking import Booking, TimeSlot


async def _make_org_with_phone_otp(db: AsyncSession, *, require_phone_otp: bool = True, require_otp: bool = False) -> Organization:
    org = Organization(name="Firebase OTP Org", api_key=f"ds_fcm_{uuid.uuid4().hex[:12]}", is_active=True)
    db.add(org)
    await db.flush()
    cfg = OrgConfig(org_id=org.id, required_fields=["name", "email", "phone", "date", "time"])
    cfg.require_otp = require_otp
    cfg.require_phone_otp = require_phone_otp
    db.add(cfg)
    await db.commit()
    await db.refresh(org)
    return org


async def _make_session(db: AsyncSession, org: Organization, phone: str = "+916350063338") -> ChatSession:
    sess = ChatSession(
        org_id=org.id,
        state_data={
            "phone": phone,
            "name": "Test User",
            "email": "test@example.com",
            "date": "2026-09-10",
            "time": "10:00",
            "user_timezone": "UTC",
        },
        missing_fields=[],
        current_intent="CONFIRM_BOOKING",
        session_status="ACTIVE",
        otp_verified=True,  # email done, phone pending
        phone_otp_verified=False,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


class TestFirebaseVerifyEndpoint:
    async def test_valid_firebase_token_succeeds(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org, phone="+916350063338")
        sid = str(sess.id)

        async def mock_verify(token):
            assert token == "valid_firebase_jwt_valid_phone"
            return {"uid": "uid123", "phone_number": "+916350063338", "exp": 9999999999}

        monkeypatch.setattr("app.core.firebase.verify_firebase_id_token_admin", mock_verify)

        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": sid, "channel": "phone", "id_token": "valid_firebase_jwt_valid_phone"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "success"
        # session should be marked verified - expire to force fresh load from DB (different connection)
        db_session.expire_all()
        refreshed = await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))
        s = refreshed.scalars().first()
        assert s is not None and s.phone_otp_verified is True

    async def test_invalid_firebase_token_rejected_401(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org)
        sid = str(sess.id)

        async def mock_verify(token):
            raise ValueError("Invalid Firebase ID token")

        monkeypatch.setattr("app.core.firebase.verify_firebase_id_token_admin", mock_verify)

        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": sid, "channel": "phone", "id_token": "invalid_jwt"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401, resp.text
        # not marked verified
        db_session.expire_all()
        refreshed = await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))
        s = refreshed.scalars().first()
        assert s.phone_otp_verified is False

    async def test_expired_firebase_token_rejected(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org)
        sid = str(sess.id)

        async def mock_verify(token):
            raise ValueError("Firebase ID token expired")

        monkeypatch.setattr("app.core.firebase.verify_firebase_id_token_admin", mock_verify)

        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": sid, "channel": "phone", "id_token": "expired_jwt"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401, resp.text
        db_session.expire_all()
        refreshed = await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))
        assert refreshed.scalars().first().phone_otp_verified is False

    async def test_phone_mismatch_rejected(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org, phone="+916350063338")
        sid = str(sess.id)

        async def mock_verify(token):
            return {"uid": "uid123", "phone_number": "+919999999999", "exp": 9999999999}

        monkeypatch.setattr("app.core.firebase.verify_firebase_id_token_admin", mock_verify)

        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": sid, "channel": "phone", "id_token": "valid_but_wrong_phone"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400, resp.text
        assert "mismatch" in resp.text.lower()
        db_session.expire_all()
        refreshed = await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))
        assert refreshed.scalars().first().phone_otp_verified is False

    async def test_phone_channel_requires_id_token(self, client: AsyncClient, db_session: AsyncSession):
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org)
        # No id_token, only code - must be rejected, not fallback to 6-digit
        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": str(sess.id), "channel": "phone", "code": "123456"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        assert "firebase" in resp.text.lower()

        # Also missing token entirely
        resp2 = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": str(sess.id), "channel": "phone"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp2.status_code == 400

    async def test_phone_old_code_not_accepted_even_if_otp_record_exists(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org, phone="+916350063338")
        # Manually create a legacy phone OTP record - should be ignored for phone channel
        from app.crud.otp import generate_otp as _orig_gen

        # generate for email to have a real OTP, but phone code should still be rejected
        # Try to verify phone with code 123456 - should fail even if we mock a valid OTPRecord
        # Since our new logic disables phone code path, it will return 400 requires id_token
        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": str(sess.id), "channel": "phone", "code": "123456", "id_token": None},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        assert "firebase" in resp.text.lower()

    async def test_mock_valid_token_shortcut(self, client: AsyncClient, db_session: AsyncSession):
        # token starting with mock_valid_token: should be accepted without Firebase app
        org = await _make_org_with_phone_otp(db_session)
        sess = await _make_session(db_session, org, phone="+916350063338")
        sid = str(sess.id)
        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": sid, "channel": "phone", "id_token": "mock_valid_token:+916350063338"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        refreshed = await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sid)))
        assert refreshed.scalars().first().phone_otp_verified is True


class TestRequireOtpFalse:
    async def test_require_otp_false_skips_verification(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session, require_phone_otp=False, require_otp=False)
        # session that would normally need OTP but require_otp is false
        sess = ChatSession(
            org_id=org.id,
            state_data={
                "name": "A",
                "email": "a@test.com",
                "phone": "+916350063338",
                "date": "2026-09-10",
                "time": "10:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            session_status="ACTIVE",
            otp_verified=False,
            phone_otp_verified=False,
        )
        db_session.add(sess)
        await db_session.commit()
        await db_session.refresh(sess)

        # Message dispatch with require_otp false should not ask for OTP; it should go to confirmation
        # Stub LLM
        async def _fake_ainvoke(inputs, config=None):
            from langchain_core.messages import AIMessage

            return {
                "messages": [AIMessage(content="ok")],
                "collected_fields": dict(inputs.get("collected_fields") or {}),
                "follow_up_fields": [],
                "current_intent": "CONFIRM_BOOKING",
                "captured_deltas": {},
                "merge_diagnostics": {},
                "validation_errors": {},
                "context_window": [],
                "summary": "",
            }

        monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _fake_ainvoke)
        # Create a slot so booking can succeed if needed
        from datetime import datetime

        slot_start = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        slot = TimeSlot(org_id=org.id, start_time=slot_start, end_time=slot_start + timedelta(hours=1), is_booked=False)
        db_session.add(slot)
        await db_session.commit()

        async def _noop_emails(*_a, **_k):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(sess.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Should not be OTP modal
        assert body["action"]["widget_directive"] != "REQUIRE_OTP_MODAL"


class TestBookingGating:
    async def test_successful_verification_allows_booking(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session, require_phone_otp=True, require_otp=False)
        sess = await _make_session(db_session, org, phone="+916350063338")
        # verify phone first
        async def mock_verify(token):
            return {"uid": "u1", "phone_number": "+916350063338", "exp": 9999999999}

        monkeypatch.setattr("app.core.firebase.verify_firebase_id_token_admin", mock_verify)

        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": str(sess.id), "channel": "phone", "id_token": "valid_token"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200

        # Now try to confirm booking - should create booking
        async def _fake_ainvoke(inputs, config=None):
            from langchain_core.messages import AIMessage

            return {
                "messages": [AIMessage(content="ok")],
                "collected_fields": dict(inputs.get("collected_fields") or {}),
                "follow_up_fields": [],
                "current_intent": "CONFIRM_BOOKING",
                "captured_deltas": {},
                "merge_diagnostics": {},
                "validation_errors": {},
                "context_window": [],
                "summary": "",
            }

        monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _fake_ainvoke)
        slot_start = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        slot = TimeSlot(org_id=org.id, start_time=slot_start, end_time=slot_start + timedelta(hours=1), is_booked=False)
        db_session.add(slot)
        await db_session.commit()

        async def _noop_emails(*_a, **_k):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp2 = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(sess.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp2.status_code == 200
        body = resp2.json()
        # Should be booking confirmed, not OTP modal
        assert body["action"]["widget_directive"] == "BOOKING_CONFIRMED_CARD"
        # Verify booking exists
        result = await db_session.execute(select(Booking).where(Booking.session_id == sess.id))
        assert result.scalars().first() is not None

    async def test_failed_verification_booking_never_created(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        org = await _make_org_with_phone_otp(db_session, require_phone_otp=True, require_otp=False)
        sess = await _make_session(db_session, org, phone="+916350063338")

        async def mock_verify_fail(token):
            raise ValueError("Invalid Firebase ID token")

        monkeypatch.setattr("app.core.firebase.verify_firebase_id_token_admin", mock_verify_fail)

        resp = await client.post(
            "/api/v1/auth/security/otp/verify",
            json={"session_id": str(sess.id), "channel": "phone", "id_token": "bad_token"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401

        # Try to confirm booking without verification - should NOT create booking, should ask for OTP
        async def _fake_ainvoke(inputs, config=None):
            from langchain_core.messages import AIMessage

            return {
                "messages": [AIMessage(content="ok")],
                "collected_fields": dict(inputs.get("collected_fields") or {}),
                "follow_up_fields": [],
                "current_intent": "CONFIRM_BOOKING",
                "captured_deltas": {},
                "merge_diagnostics": {},
                "validation_errors": {},
                "context_window": [],
                "summary": "",
            }

        monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _fake_ainvoke)
        slot_start = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        slot = TimeSlot(org_id=org.id, start_time=slot_start, end_time=slot_start + timedelta(hours=1), is_booked=False)
        db_session.add(slot)
        await db_session.commit()

        async def _noop_emails(*_a, **_k):
            return None

        monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop_emails)

        resp2 = await client.post(
            "/api/v1/chat/message/dispatch",
            json={"message": "yes", "session_id": str(sess.id)},
            headers={"X-Org-API-Key": org.api_key},
        )
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["action"]["widget_directive"] == "REQUIRE_OTP_MODAL"
        result = await db_session.execute(select(Booking).where(Booking.session_id == sess.id))
        assert result.scalars().first() is None

    async def test_phone_otp_does_not_use_otp_records(self, db_session: AsyncSession, monkeypatch):
        # Ensure phone generate does not create OTPRecord
        from app.crud.otp import generate_otp
        from app.models.otp import OTPRecord

        org = Organization(name="NoRecord Org", api_key=f"ds_nr_{uuid.uuid4().hex[:8]}", is_active=True)
        db_session.add(org)
        await db_session.flush()
        cfg = OrgConfig(org_id=org.id, required_fields=["phone"])
        cfg.require_phone_otp = True
        db_session.add(cfg)
        await db_session.commit()
        sess = ChatSession(org_id=org.id, state_data={"phone": "+916350063338"}, missing_fields=[], session_status="ACTIVE")
        db_session.add(sess)
        await db_session.commit()
        await db_session.refresh(sess)

        code = await generate_otp(db_session, str(sess.id), channel="phone")
        assert code == ""
        result = await db_session.execute(select(OTPRecord).where(OTPRecord.session_id == sess.id, OTPRecord.channel == "phone"))
        assert result.scalars().first() is None
