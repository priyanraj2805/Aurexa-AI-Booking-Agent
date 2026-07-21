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
        except asyncio.CancelledError:
            logger.info("Background cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Cleanup task error: {e}", exc_info=True)
