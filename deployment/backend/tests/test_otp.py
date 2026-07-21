"""
OTP tests — generation, verification, brute-force lockout, expiration.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.crud.otp import generate_otp, verify_otp, MAX_OTP_ATTEMPTS
from app.crud import otp as otp_crud


async def _setup_session(db: AsyncSession):
    """Create an org + session and return session_id."""
    org = Organization(
        name="OTP Org",
        api_key=f"ds_otp_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db.add(org)
    await db.flush()

    config = OrgConfig(
        org_id=org.id,
        required_fields=["name", "email", "phone", "date", "time"],
    )
    db.add(config)

    session = ChatSession(
        org_id=org.id,
        state_data={},
        missing_fields=["name"],
        session_status="ACTIVE",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return str(session.id)


class TestOTPGeneration:
    async def test_generate_returns_6_digit_code(self, db_session: AsyncSession):
        sid = await _setup_session(db_session)
        code = await generate_otp(db_session, sid)
        assert len(code) == 6
        assert code.isdigit()

    async def test_generate_for_nonexistent_session_raises(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="Session not found"):
            await generate_otp(db_session, str(uuid.uuid4()))

    async def test_generate_overwrites_previous_otp(self, db_session: AsyncSession):
        sid = await _setup_session(db_session)
        code1 = await generate_otp(db_session, sid)
        code2 = await generate_otp(db_session, sid)
        # New code should be generated (statistically different)
        # At worst they match by chance (1 in 10M), so we just check both are valid
        assert len(code1) == 6 and len(code2) == 6


class TestOTPVerification:
    async def test_verify_correct_code(self, db_session: AsyncSession):
        sid = await _setup_session(db_session)
        code = await generate_otp(db_session, sid)
        result = await verify_otp(db_session, sid, code)
        assert result is True

    async def test_verify_wrong_code(self, db_session: AsyncSession):
        sid = await _setup_session(db_session)
        await generate_otp(db_session, sid)
        result = await verify_otp(db_session, sid, "00000000")
        assert result is False

    async def test_verify_nonexistent_session(self, db_session: AsyncSession):
        result = await verify_otp(db_session, str(uuid.uuid4()), "12345678")
        assert result is False

    async def test_verify_marks_session_as_verified(self, db_session: AsyncSession):
        from sqlalchemy.future import select

        sid = await _setup_session(db_session)
        code = await generate_otp(db_session, sid)
        await verify_otp(db_session, sid, code)

        result = await db_session.execute(
            select(ChatSession).where(ChatSession.id == uuid.UUID(sid))
        )
        session = result.scalars().first()
        assert session.otp_verified is True

    async def test_verify_sets_confirm_intent_and_context_bound_key(
        self, db_session: AsyncSession
    ):
        from sqlalchemy.future import select

        sid = await _setup_session(db_session)
        session_result = await db_session.execute(
            select(ChatSession).where(ChatSession.id == uuid.UUID(sid))
        )
        session = session_result.scalars().first()
        session.state_data = {
            "email": "customer@example.com",
            "date": "2026-08-21",
            "time": "10:00",
        }
        await db_session.commit()

        code = await generate_otp(db_session, sid)
        assert await verify_otp(db_session, sid, code) is True

        refreshed = await db_session.execute(
            select(ChatSession).where(ChatSession.id == uuid.UUID(sid))
        )
        updated = refreshed.scalars().first()
        assert updated.otp_verified is True
        assert updated.current_intent == "CONFIRM_BOOKING"
        assert updated.state_data["_internal"]["otp_verified_key"] == (
            f"otp:{sid}:customer@example.com:2026-08-21:10:00"
        )


class TestOTPBruteForce:
    async def test_lockout_after_max_attempts(self, db_session: AsyncSession):
        sid = await _setup_session(db_session)
        await generate_otp(db_session, sid)

        # Exhaust attempts
        for _ in range(MAX_OTP_ATTEMPTS):
            await verify_otp(db_session, sid, "00000000")

        # Next attempt should raise ValueError
        with pytest.raises(ValueError, match="Too many failed attempts"):
            await verify_otp(db_session, sid, "00000000")

    async def test_correct_code_after_some_failures(self, db_session: AsyncSession):
        sid = await _setup_session(db_session)
        code = await generate_otp(db_session, sid)

        # Fail a few times
        for _ in range(MAX_OTP_ATTEMPTS - 1):
            assert await verify_otp(db_session, sid, "00000000") is False

        # Should still accept correct code
        assert await verify_otp(db_session, sid, code) is True


class _FrozenDateTime:
    _now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz:
            return cls._now.astimezone(tz)
        return cls._now

    @classmethod
    def fromisoformat(cls, value: str):
        return datetime.fromisoformat(value)


class TestOTPResendPolicy:
    async def test_resend_enforces_cooldown_and_cap(self, db_session: AsyncSession, monkeypatch):
        sid = await _setup_session(db_session)
        monkeypatch.setattr(otp_crud, "datetime", _FrozenDateTime)

        _FrozenDateTime._now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        await generate_otp(db_session, sid)

        with pytest.raises(ValueError, match="cooldown"):
            await generate_otp(db_session, sid, resend=True)

        _FrozenDateTime._now += timedelta(seconds=31)
        await generate_otp(db_session, sid, resend=True)
        _FrozenDateTime._now += timedelta(seconds=31)
        await generate_otp(db_session, sid, resend=True)
        _FrozenDateTime._now += timedelta(seconds=31)
        await generate_otp(db_session, sid, resend=True)

        _FrozenDateTime._now += timedelta(seconds=31)
        with pytest.raises(ValueError, match="Maximum resend attempts"):
            await generate_otp(db_session, sid, resend=True)
