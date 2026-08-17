"""Forgot-password OTP service for the super-admin control plane.

A short-lived numeric OTP is emailed to the admin's registered address;
the admin proves possession of the code before setting a new password.
The admin is NOT authenticated at this point, so tokens are keyed by email.

Security properties:
  - Stored only as a sha256 hash, never plaintext.
  - Constant-time comparison, single-use, max attempts, short TTL.
  - Resend cooldown + cap per email.
  - The forgot endpoint always answers generically (no account enumeration).
"""
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import sha256_hex

logger = logging.getLogger(__name__)

RESET_TTL_MINUTES = 15
RESET_MAX_ATTEMPTS = 5
RESET_COOLDOWN_SECONDS = 30
RESET_RESEND_CAP = 3


class ResetError(Exception):
    """User-facing password-reset policy violations."""


def generate_reset_otp() -> str:
    """6-digit numeric OTP (leading zeros preserved)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_reset_email(to_email: str, otp: str) -> bool:
    """Send the reset code via SMTP. Returns True when the send succeeded."""
    if not settings.SMTP_HOST:
        logger.warning(
            "SMTP_HOST not set — password-reset OTP to %s skipped (fail-open)",
            to_email,
        )
        return False

    subject = f"{otp} - Your DotStark Super Admin Password Reset Code"
    body = f"""\
Hello,

You (or someone else) requested a password reset for your DotStark
Super Admin account.

Your verification code is:

    {otp}

Enter this code to set a new password. It expires in {RESET_TTL_MINUTES}
minutes and can only be used once.

If you did not request this, you can safely ignore this email — your
password will not change.

— DotStark Ops
"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message.set_content(body)

    try:
        use_tls = settings.SMTP_PORT == 587
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
            if use_tls:
                client.starttls()
            if settings.SMTP_USER:
                client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            client.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Password-reset email failed: %s", exc)
        return False


async def _latest_unused(db: AsyncSession, email: str):
    return (
        await db.execute(
            text(
                """
                SELECT id, token_hash, expires_at, attempts_left, created_at
                FROM super_admin.password_reset_tokens
                WHERE email = :email AND used_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"email": email},
        )
    ).mappings().first()


async def issue_reset_token(db: AsyncSession, email: str) -> Optional[str]:
    """Issue a fresh reset OTP for the email and send it.

    Enforces a resend cooldown + cap. Returns the code when the email was
    delivered, else None (the forgot endpoint still answers generically).
    """
    email = email.lower().strip()
    now = datetime.now(timezone.utc)

    existing = await _latest_unused(db, email)
    if existing:
        created = existing["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed = (now - created).total_seconds()
        if elapsed < RESET_COOLDOWN_SECONDS:
            raise ResetError(
                f"Please wait {int(RESET_COOLDOWN_SECONDS - elapsed)}s before requesting a new code"
            )

    sent_before = (
        await db.execute(
            text(
                """
                SELECT count(*) AS n
                FROM super_admin.password_reset_tokens
                WHERE email = :email
                """
            ),
            {"email": email},
        )
    ).scalar()
    if (sent_before or 0) >= RESET_RESEND_CAP:
        raise ResetError("Maximum reset-code requests reached. Try again later.")

    if existing:
        await db.execute(
            text(
                """
                UPDATE super_admin.password_reset_tokens
                SET used_at = now()
                WHERE id = :id AND used_at IS NULL
                """
            ),
            {"id": existing["id"]},
        )

    otp = generate_reset_otp()
    expires_at = now + timedelta(minutes=RESET_TTL_MINUTES)
    await db.execute(
        text(
            """
            INSERT INTO super_admin.password_reset_tokens
                (email, token_hash, expires_at, attempts_left)
            VALUES (:email, :token_hash, :expires_at, :attempts_left)
            """
        ),
        {
            "email": email,
            "token_hash": sha256_hex(otp),
            "expires_at": expires_at,
            "attempts_left": RESET_MAX_ATTEMPTS,
        },
    )
    await db.commit()

    sent = _send_reset_email(email, otp)
    if not sent:
        # Never leave an unusable token behind (the code never reached the
        # admin's mailbox, so nobody can use it — but stay tidy anyway).
        await db.execute(
            text(
                """
                UPDATE super_admin.password_reset_tokens
                SET used_at = now()
                WHERE email = :email AND used_at IS NULL
                """
            ),
            {"email": email},
        )
        await db.commit()
        return None

    return otp


async def verify_reset_token(db: AsyncSession, email: str, otp: str) -> bool:
    """Verify a submitted reset code. Single-use; max attempts enforced."""
    email = email.lower().strip()
    now = datetime.now(timezone.utc)

    row = await _latest_unused(db, email)
    if not row:
        return False

    expires = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        await db.execute(
            text(
                """
                UPDATE super_admin.password_reset_tokens
                SET used_at = now()
                WHERE id = :id AND used_at IS NULL
                """
            ),
            {"id": row["id"]},
        )
        await db.commit()
        return False

    if row["attempts_left"] <= 0:
        await db.execute(
            text(
                """
                UPDATE super_admin.password_reset_tokens
                SET used_at = now()
                WHERE id = :id AND used_at IS NULL
                """
            ),
            {"id": row["id"]},
        )
        await db.commit()
        return False

    submitted = otp.strip()
    if not submitted or not secrets.compare_digest(row["token_hash"], sha256_hex(submitted)):
        remaining = row["attempts_left"] - 1
        await db.execute(
            text(
                """
                UPDATE super_admin.password_reset_tokens
                SET attempts_left = :remaining
                WHERE id = :id
                """
            ),
            {"id": row["id"], "remaining": remaining},
        )
        await db.commit()
        return False

    await db.execute(
        text(
            """
            UPDATE super_admin.password_reset_tokens
            SET used_at = now()
            WHERE id = :id AND used_at IS NULL
            """
        ),
        {"id": row["id"]},
    )
    await db.commit()
    return True