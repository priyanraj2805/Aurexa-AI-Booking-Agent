"""
Background tasks for periodic maintenance.

- expire_stale_sessions: marks ACTIVE sessions with no activity for 24h as EXPIRED
- cleanup_expired_otps: deletes OTP records past their expiry time
- release_deadlocked_sessions: releases sessions stuck in CONFIRMING for >10 minutes
- cleanup_expired_slot_locks: releases expired TimeSlot locks
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update, delete, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.models.session import ChatSession
from app.models.otp import OTPRecord
from app.models.config import OrgConfig
from app.core.sweeper import (
    release_deadlocked_sessions,
    cleanup_expired_slot_locks,
)
from app.core.email_delivery_service import process_due_email_retries

logger = logging.getLogger(__name__)

# Track last run for expiry reminders to avoid spamming (once per day per org)
_last_expiry_reminder_by_org: dict[str, datetime] = {}

STALE_SESSION_HOURS = 24
CLEANUP_INTERVAL_SECONDS = 300  # run every 5 minutes
_last_archival_runs_by_org: dict[str, datetime] = {}


async def expire_stale_sessions(db: AsyncSession) -> int:
    """Mark ACTIVE sessions with no activity for 24h as EXPIRED."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_SESSION_HOURS)
    result = await db.execute(
        update(ChatSession)
        .where(
            and_(
                ChatSession.session_status == "ACTIVE",
                ChatSession.updated_at < cutoff,
            )
        )
        .values(session_status="EXPIRED", is_active=False)
    )
    await db.commit()
    return result.rowcount


async def cleanup_expired_otps(db: AsyncSession) -> int:
    """Delete OTP records that have expired."""
    now = datetime.now(timezone.utc)
    result = await db.execute(delete(OTPRecord).where(OTPRecord.expires_at < now))
    await db.commit()
    return result.rowcount


def _is_archival_due(
    *,
    org_key: str,
    now: datetime,
    frequency: str,
    hour_utc: int,
    last_runs: dict[str, datetime],
) -> bool:
    """Determine if archival should run for an org at the current loop tick."""
    freq = (frequency or "daily").lower()
    last_run = last_runs.get(org_key)

    if freq == "hourly":
        return (
            last_run is None
            or last_run.year != now.year
            or last_run.month != now.month
            or last_run.day != now.day
            or last_run.hour != now.hour
        )

    # Default daily behavior
    if now.hour < hour_utc:
        return False
    return last_run is None or last_run.date() != now.date()


async def archive_inactive_sessions(db: AsyncSession, now: datetime | None = None) -> int:
    """Archive sessions older than each org's configured retention policy."""
    now = now or datetime.now(timezone.utc)
    result = await db.execute(select(OrgConfig))
    configs = result.scalars().all()

    archived_count = 0
    for cfg in configs:
        retention_days = max(7, min(int(cfg.session_retention_days or 30), 365))
        cutoff = now - timedelta(days=retention_days)

        update_result = await db.execute(
            update(ChatSession)
            .where(
                and_(
                    ChatSession.org_id == cfg.org_id,
                    ChatSession.updated_at < cutoff,
                    ChatSession.archived_at.is_(None),
                    ChatSession.session_status != "ARCHIVED",
                )
            )
            .values(
                archived_at=now,
                session_status="ARCHIVED",
                is_active=False,
            )
        )
        archived_count += update_result.rowcount or 0

    await db.commit()
    return archived_count


async def run_scheduled_archival(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    last_runs: dict[str, datetime] | None = None,
) -> int:
    """Run archival only for orgs due under configured schedule."""
    now = now or datetime.now(timezone.utc)
    run_state = last_runs if last_runs is not None else _last_archival_runs_by_org

    cfg_result = await db.execute(select(OrgConfig))
    configs = cfg_result.scalars().all()
    total_archived = 0

    for cfg in configs:
        org_key = str(cfg.org_id)
        hour_utc = int(cfg.session_cleanup_hour_utc or 0)
        frequency = cfg.session_cleanup_frequency or "daily"
        if not _is_archival_due(
            org_key=org_key,
            now=now,
            frequency=frequency,
            hour_utc=hour_utc,
            last_runs=run_state,
        ):
            continue

        retention_days = max(7, min(int(cfg.session_retention_days or 30), 365))
        cutoff = now - timedelta(days=retention_days)
        result = await db.execute(
            update(ChatSession)
            .where(
                and_(
                    ChatSession.org_id == cfg.org_id,
                    ChatSession.updated_at < cutoff,
                    ChatSession.archived_at.is_(None),
                    ChatSession.session_status != "ARCHIVED",
                )
            )
            .values(archived_at=now, session_status="ARCHIVED", is_active=False)
        )
        total_archived += result.rowcount or 0
        run_state[org_key] = now

    await db.commit()
    return total_archived


async def check_expiring_subscriptions(db: AsyncSession, now: datetime | None = None) -> int:
    """Check for subscriptions expiring in 3 days and send email + in-app notification (once per day per org)."""
    now = now or datetime.now(timezone.utc)
    # Find subscriptions expiring in exactly 3 days (between 3d and 4d from now)
    # This ensures we send once when it enters the 3-day window
    from sqlalchemy import text as sa_text

    rows = (
        await db.execute(
            sa_text(
                """
                SELECT s.org_id, s.current_period_end, p.name as plan_name, p.code as plan_code,
                       p.monthly_price_cents, o.name as org_name, u.email as owner_email, u.full_name as owner_name
                FROM super_admin.subscriptions s
                JOIN super_admin.plans p ON p.id = s.plan_id
                JOIN public.organizations o ON o.id = s.org_id
                JOIN public.users u ON u.org_id = s.org_id
                WHERE s.status = 'active'
                  AND p.monthly_price_cents > 0
                  AND s.current_period_end BETWEEN :start AND :end
                """
            ),
            {
                "start": now + timedelta(days=3),
                "end": now + timedelta(days=4),
            },
        )
    ).mappings().all()

    if not rows:
        return 0

    # Group by org_id to avoid duplicate emails per org (multiple users)
    by_org: dict[str, dict] = {}
    for r in rows:
        oid = str(r["org_id"])
        if oid not in by_org:
            by_org[oid] = dict(r)

    sent = 0
    for oid, row in by_org.items():
        # Deduplicate: only once per day per org
        last = _last_expiry_reminder_by_org.get(oid)
        if last and last.date() == now.date():
            continue

        org_name = row["org_name"] or "your organization"
        plan_name = row["plan_name"] or row["plan_code"] or "Premium"
        owner_email = row["owner_email"]
        expiry = row["current_period_end"]
        days_left = 3  # we are in the 3-day window

        # Create in-app notification for the org
        try:
            from app.models.org_notification import OrgNotification
            import uuid as uuid_mod

            notif = OrgNotification(
                id=uuid_mod.uuid4(),
                org_id=row["org_id"],
                kind="billing_expiry",
                title="Plan expiring soon",
                body=f"Your {plan_name} plan expires in {days_left} days on {expiry.strftime('%Y-%m-%d')}. Renew now to avoid service interruption.",
                resource_type="subscription",
                resource_id=str(row["org_id"]),
                is_read=False,
                created_at=now,
            )
            db.add(notif)
        except Exception as e:
            logger.warning(f"Failed to create expiry notification for org {oid}: {e}")

        # Send email via existing mail helper (use anyio to run sync)
        try:
            from app.core.mail import _send_email_sync

            subject = f"Your {plan_name} plan expires in {days_left} days"
            body_html = f"""
            <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px;">
              <h2 style="margin: 0 0 12px; color: #0f172a;">Your {plan_name} plan expires soon</h2>
              <p style="margin: 0 0 12px; color: #334155;">Hi {row['owner_name'] or 'there'},</p>
              <p style="margin: 0 0 12px; color: #334155;">Your <strong>{plan_name}</strong> plan for <strong>{org_name}</strong> expires on <strong>{expiry.strftime('%Y-%m-%d')}</strong> — in {days_left} days.</p>
              <p style="margin: 0 0 16px; color: #334155;">Renew now from your admin dashboard to avoid service interruption and keep your bookings active.</p>
              <a href="{'http://localhost:3002/dashboard/billing'}" style="display:inline-block; background:#6366f1; color:#fff; padding:10px 16px; border-radius:8px; text-decoration:none; font-weight:600;">Renew in Billing & Plans</a>
              <p style="margin: 16px 0 0; color: #64748b; font-size: 12px;">If you already renewed, please ignore this reminder.</p>
            </div>
            """
            # Run sync email in thread
            import anyio

            await anyio.to_thread.run_sync(_send_email_sync, owner_email, subject, body_html)
            logger.info(f"Sent expiry reminder to {owner_email} for org {oid}")
        except Exception as e:
            logger.warning(f"Failed to send expiry email to {owner_email} for org {oid}: {e}")

        _last_expiry_reminder_by_org[oid] = now
        sent += 1

    if sent:
        await db.commit()
    return sent


async def run_cleanup_loop():
    """Long-running loop that executes cleanup tasks periodically."""
    logger.info(
        f"Background cleanup task started (interval: {CLEANUP_INTERVAL_SECONDS}s)"
    )
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            async with AsyncSessionLocal() as db:
                expired = await expire_stale_sessions(db)
                if expired:
                    logger.info(f"Expired {expired} stale session(s)")

                otps = await cleanup_expired_otps(db)
                if otps:
                    logger.info(f"Cleaned up {otps} expired OTP(s)")

                # Release deadlocked sessions (stuck in CONFIRMING for >10 min)
                released = await release_deadlocked_sessions(db)
                if released:
                    logger.info(f"Released {len(released)} deadlocked session(s)")

                # Clean up expired slot locks
                locks = await cleanup_expired_slot_locks(db)
                if locks:
                    logger.info(f"Released {locks} expired slot lock(s)")

                archived = await run_scheduled_archival(db)
                if archived:
                    logger.info(f"Archived {archived} inactive session(s)")

                retries = await process_due_email_retries()
                if retries:
                    logger.info(f"Processed {retries} due email retry record(s)")

                # Check for expiring subscriptions (3-day reminder)
                expiring = await check_expiring_subscriptions(db)
                if expiring:
                    logger.info(f"Sent {expiring} plan expiry reminder(s)")
        except asyncio.CancelledError:
            logger.info("Background cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Cleanup task error: {e}", exc_info=True)
