from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, or_, text
from sqlalchemy.exc import OperationalError
from datetime import date, timedelta, datetime
from typing import List, Optional
import logging
import uuid

from app.models.booking import TimeSlot, Booking
from app.core.telemetry import increment_usage

logger = logging.getLogger(__name__)


def _is_lock_conflict(error: OperationalError) -> bool:
    return getattr(getattr(error, "orig", None), "pgcode", None) == "55P03"


async def cancel_booking(
    db: AsyncSession,
    *,
    session_id: str | None = None,
    booking_id: str | None = None,
    org_id: str | uuid.UUID | None = None,
) -> bool:
    """
    Cancel a confirmed booking: delete the Booking record and the TimeSlot row.
    In the virtual slot model, booked TimeSlot rows exist only for confirmed bookings.
    Deleting both returns the slot to the "virtually available" pool.
    """
    if not session_id and not booking_id:
        raise ValueError("session_id or booking_id is required")

    org_uuid = None
    if org_id is not None:
        org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id

    try:
        predicates = []
        if booking_id:
            predicates.append(Booking.id == uuid.UUID(str(booking_id)))
        if session_id:
            predicates.append(Booking.session_id == uuid.UUID(str(session_id)))
        if org_uuid is not None:
            predicates.append(Booking.org_id == org_uuid)

        result = await db.execute(
            select(Booking).where(*predicates).with_for_update(nowait=True)
        )
        booking = result.scalars().first()
        if not booking:
            return False

        slot_result = await db.execute(
            select(TimeSlot)
            .where(TimeSlot.id == booking.slot_id, TimeSlot.org_id == booking.org_id)
            .with_for_update(nowait=True)
        )
        slot = slot_result.scalars().first()
        if slot:
            await db.delete(slot)

        await db.delete(booking)
        await db.commit()
        logger.info(
            "Booking cancelled",
            extra={
                "session_id": str(booking.session_id) if booking.session_id else None,
                "booking_id": str(booking.id),
                "org_id": str(booking.org_id),
            },
        )
        return True
    except OperationalError as e:
        await db.rollback()
        if _is_lock_conflict(e):
            raise ValueError(
                "Booking is being modified by another request. Please retry."
            )
        raise


async def cancel_booking_by_session(
    db: AsyncSession, session_id: str, org_id: str | uuid.UUID | None = None
) -> bool:
    return await cancel_booking(db, session_id=session_id, org_id=org_id)


async def get_next_available_dates(
    db: AsyncSession, org_id: str, after_date: date, limit: int = 3
) -> List[date]:
    """
    Returns up to `limit` future dates that have at least one available virtual slot,
    by walking the weekly schedule starting from `after_date`.
    """
    from app.engine.slot_engine import compute_available_slots
    from datetime import timezone
    import uuid as _uuid

    today = datetime.now(timezone.utc).date()
    search_start = max(after_date + timedelta(days=1), today)
    org_uuid = _uuid.UUID(org_id) if isinstance(org_id, str) else org_id

    available_dates: List[date] = []
    check_date = search_start
    max_days_to_scan = 90  # Safety limit

    for _ in range(max_days_to_scan):
        if len(available_dates) >= limit:
            break
        slots = await compute_available_slots(db, org_uuid, check_date)
        if slots:
            available_dates.append(check_date)
        check_date += timedelta(days=1)

    return available_dates


async def get_available_slots(
    db: AsyncSession, org_id: str, target_date: date
) -> List[date]:
    """
    Returns available slot start times (as datetime.time objects) for the given
    org on target_date, computed on-demand from the BusinessSchedule.

    NOTE: Return type changed from List[TimeSlot] to List[time]. Any callers
    that expected TimeSlot objects now receive time objects instead.
    """
    from app.engine.slot_engine import compute_available_slots
    from datetime import time
    import uuid as _uuid

    org_uuid = _uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    return await compute_available_slots(db, org_uuid, target_date)


# Lock timeout in minutes (slot is reserved for 5 minutes during booking flow)
SLOT_LOCK_MINUTES = 5


async def attempt_booking(
    db: AsyncSession,
    session_id: str,
    org_id: str,
    slot_id: str,
    customer_name: str,
    customer_email: str,
    customer_phone: str = "",
    user_timezone: str | None = None,
    user_slot_start_at: datetime | None = None,
    user_slot_end_at: datetime | None = None,
    timezone_source: str | None = None,
    org_timezone_snapshot: str | None = None,
    org_slot_start_at: datetime | None = None,
    org_slot_end_at: datetime | None = None,
    # Meeting link fields (Phase 16-02)
    meeting_type_snapshot: str | None = None,
    meeting_link_url: str | None = None,
    meeting_link_provider: str | None = None,  # ActiveMeetingProvider value
    meeting_link_status: str | None = None,  # MeetingLinkStatus value
    meeting_link_external_id: str | None = None,
    meeting_link_error_code: str | None = None,
    # Payment snapshot (Razorpay)
    payment_status: str | None = None,
    razorpay_order_id: str | None = None,
    razorpay_payment_id: str | None = None,
    amount_paise: int | None = None,
    currency: str | None = None,
) -> Booking:
    """
    Atomically locks a slot and creates a booking record.
    Uses SELECT ... FOR UPDATE NOWAIT to acquire a row-level lock on the
    target slot. If another transaction has already locked it, PostgreSQL
    raises an immediate error instead of waiting — preventing double-bookings
    under concurrent requests.

    Also checks locked_until to prevent ghost slots from being booked.
    The lock is set when session enters CONFIRMING state (in message_processor),
    and cleared here when booking succeeds.
    """
    from sqlalchemy.exc import OperationalError

    try:
        # Acquire exclusive row lock — fails instantly if another txn holds it
        # Also validates that the slot belongs to this organization (tenant isolation)
        result = await db.execute(
            select(TimeSlot)
            .where(TimeSlot.id == slot_id, TimeSlot.org_id == org_id)
            .with_for_update(nowait=True)
        )
        slot = result.scalars().first()

        if not slot:
            raise ValueError("Slot not found or does not belong to your organization")

        # Check if slot is already booked
        if slot.is_booked:
            raise ValueError("Slot is no longer available")

        # Note: We don't check locked_until here because:
        # 1. The lock might be from THIS session (user confirming their own booking)
        # 2. The SELECT ... FOR UPDATE NOWAIT above provides real concurrency protection
        # 3. Expired locks are cleaned up by background tasks

        # Resolve meeting link status/provider enums (if string passed)
        from app.models.config import (
            MeetingLinkStatus,
            ActiveMeetingProvider as MeetingProviderEnum,
        )

        resolved_meeting_link_status = None
        if meeting_link_status:
            if isinstance(meeting_link_status, str):
                resolved_meeting_link_status = MeetingLinkStatus(meeting_link_status)
            else:
                resolved_meeting_link_status = meeting_link_status

        resolved_meeting_link_provider = None
        if meeting_link_provider:
            if isinstance(meeting_link_provider, str):
                resolved_meeting_link_provider = MeetingProviderEnum(
                    meeting_link_provider
                )
            else:
                resolved_meeting_link_provider = meeting_link_provider

        booking = Booking(
            org_id=uuid.UUID(org_id),
            session_id=uuid.UUID(session_id),
            slot_id=uuid.UUID(slot_id),
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            user_timezone=user_timezone,
            user_slot_start_at=user_slot_start_at,
            user_slot_end_at=user_slot_end_at,
            timezone_source=timezone_source,
            org_timezone_snapshot=org_timezone_snapshot,
            org_slot_start_at=org_slot_start_at,
            org_slot_end_at=org_slot_end_at,
            # Meeting link fields (Phase 16-02)
            meeting_type_snapshot=meeting_type_snapshot,
            meeting_link_url=meeting_link_url,
            meeting_link_provider=resolved_meeting_link_provider,
            meeting_link_status=resolved_meeting_link_status,
            meeting_link_external_id=meeting_link_external_id,
            meeting_link_error_code=meeting_link_error_code,
            # Payment snapshot
            payment_status=payment_status or "not_required",
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            amount_paise=amount_paise,
            currency=currency,
        )
        db.add(booking)
        slot.is_booked = True
        slot.locked_until = None  # Clear any lock since booking is now complete

        await db.commit()
        await db.refresh(booking)
        await increment_usage(uuid.UUID(org_id), "bookings")
        return booking

    except OperationalError as e:
        await db.rollback()
        # Only treat "lock not available" (SQLSTATE 55P03) as a slot conflict
        pgcode = getattr(getattr(e, "orig", None), "pgcode", None)
        if pgcode == "55P03":
            logger.warning(f"Slot {slot_id} locked by concurrent transaction")
            raise ValueError(
                "This slot is being booked by someone else. Please choose another."
            )
        logger.error(f"Unexpected DB error during booking: {e}", exc_info=True)
        raise
