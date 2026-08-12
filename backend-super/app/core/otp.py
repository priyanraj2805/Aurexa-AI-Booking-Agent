"""Org-admin consent OTP service (Phase 13).

A short-lived numeric OTP is emailed to the org admin (the email the
organization registers with in the admin panel). The super admin must enter
it before an impersonation session can be confirmed or a conversation
transcript can be peeked.

Security properties:
  - The OTP is stored only as a sha256 hash, never plaintext.
  - Constant-time comparison, single-use, max attempts, short TTL.
  - One active grant per (admin_id, org_id, purpose); issuing a new one
    invalidates the previous and enforces a resend cooldown.
  - Fail-closed: if SMTP is not configured the caller raises and no OTP is
    ever exposed in an API response.
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.config import settings
from app.core.impersonation_notify import send_otp_email
from app.core.security import sha256_hex

PURPOSE_IMPERSONATION = "impersonation"
PURPOSE_PEEK = "peek"


def generate_otp() -> str:
    """6-digit numeric OTP (leading zeros preserved)."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def _invalidate_active(
    db: AsyncSession,
    purpose: str,
    admin_id: UUID,
    org_id: UUID,
) -> None:
    await db.execute(
        text(
            """
            UPDATE super_admin.otp_grants
            SET used_at = now()
            WHERE purpose = :purpose
              AND admin_id = :admin_id
              AND org_id = :org_id
              AND used_at IS NULL
            """
        ),
        {"purpose": purpose, "admin_id": admin_id, "org_id": org_id},
    )


async def _active_grant(
    db: AsyncSession,
    purpose: str,
    admin_id: UUID,
    org_id: UUID,
):
    return (
        await db.execute(
            text(
                """
                SELECT id, token_hash, expires_at, attempts_left, used_at
                FROM super_admin.otp_grants
                WHERE purpose = :purpose
                  AND admin_id = :admin_id
                  AND org_id = :org_id
                  AND used_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"purpose": purpose, "admin_id": admin_id, "org_id": org_id},
        )
    ).mappings().first()


def _email_skipped_ok() -> bool:
    """True when SMTP is configured so sending is possible."""
    return bool(settings.SMTP_HOST)


async def issue_otp(
    db: AsyncSession,
    *,
    purpose: str,
    admin_id: UUID,
    org_id: UUID,
    org_name: str,
    recipient_email: str,
    actor_email: str,
    cooldown_seconds: Optional[int] = None,
) -> dict:
    """Issue a fresh OTP for an admin+org+purpose.

    Enforces a resend cooldown against the previously issued grant and
    invalidates it in favour of the new code. Raises HTTPException 502 when
    SMTP is not configured (fail-closed) and 429 when the cooldown has not
    elapsed.
    """
    from fastapi import HTTPException

    if not _email_skipped_ok():
        raise HTTPException(
            status_code=502,
            detail="Email delivery is not configured — cannot send an OTP to the org admin",
        )

    cooldown = cooldown_seconds or settings.OTP_RESEND_COOLDOWN_SECONDS
    existing = await _active_grant(db, purpose, admin_id, org_id)
    if existing:
        issued_at = (
            await db.execute(
                text(
                    """
                    SELECT created_at FROM super_admin.otp_grants
                    WHERE id = :id
                    """
                ),
                {"id": existing["id"]},
            )
        ).scalar()
        elapsed = (datetime.now(timezone.utc) - issued_at).total_seconds()
        if elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {remaining}s before requesting a new OTP",
            )
        await _invalidate_active(db, purpose, admin_id, org_id)

    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.OTP_TTL_MINUTES
    )
    row_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.otp_grants
                    (purpose, admin_id, org_id, recipient_email,
                     token_hash, expires_at, attempts_left)
                VALUES (:purpose, :admin_id, :org_id, :recipient_email,
                        :token_hash, :expires_at, :attempts_left)
                RETURNING id
                """
            ),
            {
                "purpose": purpose,
                "admin_id": admin_id,
                "org_id": org_id,
                "recipient_email": recipient_email,
                "token_hash": sha256_hex(otp),
                "expires_at": expires_at,
                "attempts_left": settings.OTP_MAX_ATTEMPTS,
            },
        )
    ).scalar()
    await db.commit()

    sent = send_otp_email(recipient_email, org_name, otp, purpose)
    if not sent:
        await db.execute(
            text("UPDATE super_admin.otp_grants SET used_at = now() WHERE id = :id"),
            {"id": row_id},
        )
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="Failed to deliver the OTP email — no code was issued",
        )

    await write_audit_log(
        actor_email=actor_email,
        action="otp.issue",
        resource_type="otp_grant",
        resource_id=str(row_id),
        new_values={
            "purpose": purpose,
            "org_id": str(org_id),
            "org_name": org_name,
            "recipient_email": recipient_email,
            "expires_at": expires_at.isoformat(),
        },
    )
    return {
        "grant_id": str(row_id),
        "sent_to": recipient_email,
        "expires_at": expires_at.isoformat(),
        "ttl_minutes": settings.OTP_TTL_MINUTES,
    }


async def verify_otp(
    db: AsyncSession,
    *,
    purpose: str,
    admin_id: UUID,
    org_id: UUID,
    otp: str,
    actor_email: str,
) -> dict:
    """Verify a submitted OTP. Single-use; 5 attempts max.

    Returns the grant id on success. Raises HTTPException 410 (expired),
    429 (no attempts left), 403 (invalid code).
    """
    from fastapi import HTTPException

    row = await _active_grant(db, purpose, admin_id, org_id)
    if not row:
        raise HTTPException(
            status_code=410,
            detail="No active OTP for this organization — request a new one",
        )

    now = datetime.now(timezone.utc)
    if now > row["expires_at"]:
        await db.execute(
            text("UPDATE super_admin.otp_grants SET used_at = now() WHERE id = :id"),
            {"id": row["id"]},
        )
        await db.commit()
        raise HTTPException(status_code=410, detail="OTP expired — request a new one")

    if row["attempts_left"] <= 0:
        await db.execute(
            text("UPDATE super_admin.otp_grants SET used_at = now() WHERE id = :id"),
            {"id": row["id"]},
        )
        await db.commit()
        raise HTTPException(status_code=429, detail="Too many failed attempts — request a new OTP")

    submitted = otp.strip()
    if not submitted or not secrets.compare_digest(row["token_hash"], sha256_hex(submitted)):
        remaining = row["attempts_left"] - 1
        await db.execute(
            text(
                """
                UPDATE super_admin.otp_grants
                SET attempts_left = :remaining
                WHERE id = :id
                """
            ),
            {"id": row["id"], "remaining": remaining},
        )
        await db.commit()
        await write_audit_log(
            actor_email=actor_email,
            action="otp.verify.fail",
            resource_type="otp_grant",
            resource_id=str(row["id"]),
            new_values={
                "purpose": purpose,
                "org_id": str(org_id),
                "attempts_left": remaining,
            },
        )
        raise HTTPException(
            status_code=403,
            detail=f"Invalid OTP — {remaining} attempt(s) remaining",
        )

    await db.execute(
        text(
            """
            UPDATE super_admin.otp_grants
            SET used_at = now()
            WHERE id = :id AND used_at IS NULL
            """
        ),
        {"id": row["id"]},
    )
    await db.commit()
    await write_audit_log(
        actor_email=actor_email,
        action="otp.verify.ok",
        resource_type="otp_grant",
        resource_id=str(row["id"]),
        new_values={"purpose": purpose, "org_id": str(org_id)},
    )
    return {"grant_id": str(row["id"])}


async def has_valid_grant(
    db: AsyncSession,
    *,
    purpose: str,
    admin_id: UUID,
    org_id: UUID,
    ttl_minutes: Optional[int] = None,
) -> bool:
    """True when this admin+org has a verified (used) grant still inside the
    peek window. Used to skip the OTP prompt for repeated transcript views."""
    ttl = ttl_minutes or settings.PEEK_GRANT_TTL_MINUTES
    row = (
        await db.execute(
            text(
                """
                SELECT 1 FROM super_admin.otp_grants
                WHERE purpose = :purpose
                  AND admin_id = :admin_id
                  AND org_id = :org_id
                  AND used_at IS NOT NULL
                  AND used_at > now() - (:ttl * interval '1 minute')
                ORDER BY used_at DESC
                LIMIT 1
                """
            ),
            {"purpose": purpose, "admin_id": admin_id, "org_id": org_id, "ttl": ttl},
        )
    ).first()
    return row is not None
