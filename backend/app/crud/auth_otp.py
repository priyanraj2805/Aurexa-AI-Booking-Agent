"""Auth OTP generation/verification for admin-panel registration.

Unlike `app.crud.otp` (booking-flow OTP tied to a chat session), these OTPs
are keyed by (email, purpose) and live in `auth_otp_records`. Login is a
plain email + password flow — OTPs are only used to verify new accounts.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.otp import AuthOTPRecord

OTP_PURPOSES = ("register", "password_reset")

AUTH_OTP_MAX_ATTEMPTS = 5
AUTH_OTP_COOLDOWN_SECONDS = 30
AUTH_OTP_RESEND_CAP = 3
AUTH_OTP_VALID_FOR_SECONDS = 900


class OTPError(Exception):
    """Raised for user-facing OTP policy violations."""


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _parse_iso_datetime(raw) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def generate_auth_otp(
    db: AsyncSession,
    email: str,
    purpose: Literal["register", "password_reset"],
    *,
    resend: bool = False,
) -> str:
    """Generate and persist a 6-digit OTP for (email, purpose); returns the code."""
    email = email.lower().strip()
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(AuthOTPRecord).where(
            AuthOTPRecord.email == email, AuthOTPRecord.purpose == purpose
        )
    )
    record = result.scalars().first()

    if record:
        last_sent = _parse_iso_datetime(record.last_sent_at)
        if resend and last_sent:
            elapsed = (now - last_sent).total_seconds()
            if elapsed < AUTH_OTP_COOLDOWN_SECONDS:
                raise OTPError(
                    "OTP resend cooldown active. Please wait before retrying."
                )
        if resend and record.resend_count >= AUTH_OTP_RESEND_CAP:
            raise OTPError("Maximum resend attempts reached. Please try again later.")

    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = _hash_code(code)
    expires = now + timedelta(seconds=AUTH_OTP_VALID_FOR_SECONDS)

    if record:
        record.code_hash = code_hash
        record.expires_at = expires
        record.failed_attempts = 0
        record.consumed_at = None
        if resend:
            record.resend_count = (record.resend_count or 0) + 1
        record.last_sent_at = now
    else:
        record = AuthOTPRecord(
            email=email,
            purpose=purpose,
            code_hash=code_hash,
            expires_at=expires,
            failed_attempts=0,
            resend_count=1 if resend else 0,
            last_sent_at=now,
        )
        db.add(record)

    await db.commit()
    return code


async def verify_auth_otp(
    db: AsyncSession,
    email: str,
    purpose: Literal["register", "password_reset"],
    code: str,
) -> bool:
    """Verify a code for (email, purpose) with expiry + brute-force protection."""
    email = email.lower().strip()
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(AuthOTPRecord).where(
            AuthOTPRecord.email == email, AuthOTPRecord.purpose == purpose
        )
    )
    record = result.scalars().first()
    if not record:
        return False

    if record.consumed_at is not None:
        return False

    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        await db.delete(record)
        await db.commit()
        return False

    if record.failed_attempts >= AUTH_OTP_MAX_ATTEMPTS:
        await db.delete(record)
        await db.commit()
        raise OTPError("Too many failed attempts. Please request a new code.")

    if _hash_code(code) == record.code_hash:
        record.consumed_at = now
        await db.delete(record)
        await db.commit()
        return True

    record.failed_attempts = (record.failed_attempts or 0) + 1
    await db.commit()
    return False
