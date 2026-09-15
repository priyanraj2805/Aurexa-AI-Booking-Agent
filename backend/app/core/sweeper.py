"""
Background task sweeper for releasing deadlocked sessions.

Releases sessions stuck in CONFIRMING state for >10 minutes and their
associated TimeSlot locks to prevent permanent deadlocks.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import ChatSession
from app.models.booking import TimeSlot

logger = logging.getLogger(__name__)

CONFIRMING_TIMEOUT_MINUTES = 10


async def release_deadlocked_sessions(db: AsyncSession) -> List[str]:
    """
    Find and release sessions stuck in CONFIRMING state for >10 minutes.

    This handles cases where:
    - User received OTP but never completed verification
    - User closed browser during confirmation
    - Downstream booking API failed

    NOTE: This only resets session status. TimeSlot lock cleanup is handled
    separately by cleanup_expired_slot_locks() to avoid releasing locks
    that belong to active users.

    Returns list of released session IDs.
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(
        minutes=CONFIRMING_TIMEOUT_MINUTES
    )

    result = await db.execute(
        select(ChatSession).where(
            and_(
                ChatSession.session_status == "CONFIRMING",
                ChatSession.updated_at < cutoff_time,
            )
        )
    )
    stuck_sessions = result.scalars().all()

    released_session_ids = []

    for session in stuck_sessions:
        logger.warning(
            f"Releasing deadlocked session {session.session_id} "
            f"(stuck in CONFIRMING since {session.updated_at})"
        )
        released_session_ids.append(str(session.session_id))

        session.session_status = "ACTIVE"
        required_fields = (
            session.required_fields
            if hasattr(session, "required_fields") and session.required_fields
            else ["date", "time"]
        )
        session.missing_fields = required_fields

    if released_session_ids:
        await db.commit()
        logger.info(f"Released {len(released_session_ids)} deadlocked sessions")

    return released_session_ids


async def cleanup_expired_slot_locks(db: AsyncSession) -> int:
    """
    Delete any lock-only TimeSlot rows whose lock has expired (locked_until < now).

    In the virtual slot model, TimeSlot rows are created at lock time and should
    only persist as confirmed bookings (is_booked=True). Lock-only rows that
    expire should be deleted — this returns the slot to the virtual available pool.

    Returns count of deleted lock rows.
    """
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(TimeSlot).where(
            and_(
                TimeSlot.locked_until != None,
                TimeSlot.locked_until < now,
                TimeSlot.is_booked == False,
            )
        )
    )
    expired_locks = result.scalars().all()

    count = 0
    for slot in expired_locks:
        logger.info(f"Deleting expired lock-only slot row {slot.id}")
        await db.delete(slot)
        count += 1

    if count > 0:
        await db.commit()
        logger.info(f"Deleted {count} expired lock-only slot rows")

    return count


async def cleanup_expired_directives(db: AsyncSession) -> int:
    """Expire PENDING directives past expires_at. Used by sweeper (no new table impact)."""
    from app.engine.booking_directive.service import expire_stale

    try:
        count = await expire_stale(db)
        if count:
            await db.commit()
            logger.info(f"Expired {count} stale booking directives")
        return count
    except Exception as exc:  # pragma: no cover - never break slot cleanup
        logger.warning(f"Directive expire failed: {exc}")
        return 0
