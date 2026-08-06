"""
Virtual Slot Engine — computes available booking times on-demand from
a BusinessSchedule template + ScheduleException overrides.

No pre-generated rows. The time_slots table holds ONLY locked/confirmed
bookings. Availability is purely computed here.
"""

import uuid
import logging
from datetime import date, time, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_

from app.models.booking import (
    BusinessSchedule,
    ScheduleDay,
    ScheduleException,
    ExceptionType,
    TimeSlot,
)
from app.models.config import OrgConfig, CalendarProviderType

from app.core.integrations.calendar_providers.factory import get_provider
from app.engine.scheduling_policy import (
    evaluate_same_day_policy,
    evaluate_slot_cutoff_policy,
)

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo


def _slots_for_range(
    open_t: time,
    close_t: time,
    duration_minutes: int,
    break_start: Optional[time],
    break_end: Optional[time],
) -> List[time]:
    """Generate all slot start times within open→close, skipping break window."""
    slots: List[time] = []
    current = datetime.combine(date.today(), open_t)
    close_dt = datetime.combine(date.today(), close_t)
    delta = timedelta(minutes=duration_minutes)

    while current + delta <= close_dt:
        slot_start = current.time()
        slot_end = (current + delta).time()

        # Skip if slot overlaps with break
        if break_start and break_end:
            # slot overlaps break if: slot_start < break_end AND slot_end > break_start
            overlaps_break = slot_start < break_end and slot_end > break_start
            if overlaps_break:
                current += delta
                continue

        slots.append(slot_start)
        current += delta

    return slots


async def compute_slot_inventory(
    db: AsyncSession,
    org_id: uuid.UUID,
    target_date: date,
    org_config: Optional[OrgConfig] = None,
) -> Tuple[List[dict], str]:
    """
    Build full slot inventory for a date.

    Returns:
      - list of slot dicts: {start_time, start_utc, end_utc, available, reason}
      - date-level reason code when inventory is empty (or "ok")
    """
    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_id)
    )
    schedule = schedule_result.scalars().first()
    if not schedule:
        return [], "no_schedule"

    duration = schedule.slot_duration_minutes

    weekday = target_date.weekday()
    day_result = await db.execute(
        select(ScheduleDay).where(
            ScheduleDay.org_id == org_id,
            ScheduleDay.day_of_week == weekday,
        )
    )
    day = day_result.scalars().first()

    if not day or not day.is_active:
        return [], "closed_day"

    open_t = day.open_time
    close_t = day.close_time
    break_start = day.break_start
    break_end = day.break_end

    exc_result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.org_id == org_id,
            ScheduleException.exception_date == target_date,
        )
    )
    exception = exc_result.scalars().first()

    if exception:
        if exception.exception_type == ExceptionType.CLOSED:
            return [], "exception_closed"
        elif exception.exception_type == ExceptionType.CUSTOM_HOURS:
            if exception.custom_open and exception.custom_close:
                open_t = exception.custom_open
                close_t = exception.custom_close
                break_start = None
                break_end = None

    schedule_tz = (getattr(schedule, "timezone", None) or "UTC").strip()
    try:
        business_zone = ZoneInfo(schedule_tz)
    except Exception:
        business_zone = ZoneInfo("UTC")

    candidates = _slots_for_range(open_t, close_t, duration, break_start, break_end)
    if not candidates:
        return [], "no_slots_in_window"

    candidates_utc_map: dict[time, datetime] = {}
    for local_t in candidates:
        local_start = datetime.combine(target_date, local_t, tzinfo=business_zone)
        candidates_utc_map[local_t] = local_start.astimezone(timezone.utc)

    local_day_start = datetime.combine(target_date, time.min, tzinfo=business_zone)
    local_day_end = local_day_start + timedelta(days=1)
    start_dt = local_day_start.astimezone(timezone.utc)
    end_dt = local_day_end.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)

    existing_result = await db.execute(
        select(TimeSlot).where(
            and_(
                TimeSlot.org_id == org_id,
                TimeSlot.start_time >= start_dt,
                TimeSlot.start_time < end_dt,
            )
        )
    )
    existing_slots = existing_result.scalars().all()

    taken_utc: dict[datetime, str] = {}
    for slot in existing_slots:
        slot_start_utc = (
            slot.start_time.replace(tzinfo=timezone.utc)
            if slot.start_time.tzinfo is None
            else slot.start_time.astimezone(timezone.utc)
        )
        if slot.is_booked:
            taken_utc[slot_start_utc] = "booked"
        elif slot.locked_until:
            locked_until_utc = (
                slot.locked_until.replace(tzinfo=timezone.utc)
                if slot.locked_until.tzinfo is None
                else slot.locked_until.astimezone(timezone.utc)
            )
            if locked_until_utc > now:
                taken_utc[slot_start_utc] = "locked"

    external_busy: List[datetime] = []
    if org_config and org_config.active_calendar_provider != CalendarProviderType.NONE:
        provider = get_provider(org_config.active_calendar_provider.value.lower())
        if provider and await provider.is_connected(org_config):
            try:
                external_busy = await provider.get_busy_periods(
                    org_config, start_dt, end_dt, db
                )
            except Exception as e:
                logger.warning(f"Failed to fetch external busy periods: {e}")

    def conflicts_with_external(candidate_start: datetime) -> bool:
        candidate_end = candidate_start + timedelta(minutes=duration)
        for busy_start, busy_end in external_busy:
            if candidate_start < busy_end and candidate_end > busy_start:
                return True
        return False

    inventory: list[dict] = []
    for local_t, start_utc in candidates_utc_map.items():
        reason = None
        available = True
        if start_utc in taken_utc:
            available = False
            reason = taken_utc[start_utc]
        elif conflicts_with_external(start_utc):
            available = False
            reason = "external_busy"

        inventory.append(
            {
                "start_time": local_t,
                "start_utc": start_utc,
                "end_utc": start_utc + timedelta(minutes=duration),
                "available": available,
                "reason": reason,
            }
        )

    if any(item["available"] for item in inventory):
        return inventory, "ok"
    return inventory, "fully_booked"


def apply_scheduling_policy_to_inventory(
    inventory: List[dict],
    target_date: date,
    *,
    base_status: Optional[str] = None,
    effective_timezone: str,
    allow_same_day_booking: bool,
    slot_cutoff_minutes: Optional[int],
    now_utc: Optional[datetime] = None,
) -> Tuple[List[dict], str, Optional[str]]:
    """
    Apply same-day + cutoff policy in effective display timezone.

    Returns:
      - policy-adjusted inventory
      - availability status (ok/fully_booked/same_day_disabled or preserved upstream status)
      - policy reason code (same_day_disabled/past_time_cutoff/None)
    """
    evaluation_now = now_utc or datetime.now(timezone.utc)

    same_day = evaluate_same_day_policy(
        target_date=target_date,
        now_utc=evaluation_now,
        effective_timezone=effective_timezone,
        allow_same_day_booking=allow_same_day_booking,
    )
    if not same_day["allowed"]:
        return [], "same_day_disabled", str(same_day["reason"])

    if not inventory:
        preserved_status = base_status if base_status and base_status != "ok" else "fully_booked"
        return [], preserved_status, None

    adjusted: List[dict] = []
    for item in inventory:
        next_item = dict(item)
        if next_item.get("available"):
            cutoff = evaluate_slot_cutoff_policy(
                slot_start_utc=next_item["start_utc"],
                now_utc=evaluation_now,
                effective_timezone=effective_timezone,
                slot_cutoff_minutes=slot_cutoff_minutes,
            )
            if not cutoff["allowed"]:
                next_item["available"] = False
                next_item["reason"] = cutoff["reason"]
        adjusted.append(next_item)

    if any(item.get("available") for item in adjusted):
        return adjusted, "ok", None
    if base_status and base_status != "ok":
        return adjusted, base_status, None
    if adjusted and all(item.get("reason") == "past_time_cutoff" for item in adjusted):
        return adjusted, "fully_booked", "past_time_cutoff"
    return adjusted, "fully_booked", None


async def compute_available_slots(
    db: AsyncSession,
    org_id: uuid.UUID,
    target_date: date,
    org_config: Optional[OrgConfig] = None,
) -> List[time]:
    """
    Returns list of available slot start times for `org_id` on `target_date`.

    Algorithm:
    1. Load BusinessSchedule (return [] if not configured)
    2. Find ScheduleDay for the target weekday (return [] if inactive)
    3. Check ScheduleException for the specific date:
       - CLOSED  → return []
       - CUSTOM_HOURS → override open/close
    4. Generate candidate slot times from the open/close window
    5. Load existing TimeSlot rows for that org+date (booked OR locked)
    6. If org_config provided and active calendar provider, fetch external busy periods
    7. Filter out candidates that conflict with internal OR external busy periods
    8. Return remaining times
    """
    inventory, _ = await compute_slot_inventory(db, org_id, target_date, org_config)
    return [item["start_utc"].time() for item in inventory if item["available"]]


async def create_timeslot_lock(
    db: AsyncSession,
    org_id: uuid.UUID,
    target_date: date,
    requested_time: time,
    duration_minutes: int,
    lock_minutes: int = 5,
) -> Optional[TimeSlot]:
    """
    Creates (or re-uses expired lock on) a TimeSlot row for the given slot,
    sets locked_until, and returns the slot.

    Called during booking flow when user is about to confirm — reserves the slot
    without marking it booked yet. Sweeper deletes rows where is_booked=False
    and locked_until has expired.

    Returns None if slot is already booked/actively locked by another user.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.exc import IntegrityError

    # Load schedule for slot duration to compute end_time
    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_id)
    )
    schedule = schedule_result.scalars().first()
    slot_duration = (
        duration_minutes
        if duration_minutes
        else (schedule.slot_duration_minutes if schedule else 60)
    )

    schedule_tz = (
        schedule.timezone if schedule and getattr(schedule, "timezone", None) else "UTC"
    ).strip()
    try:
        business_zone = ZoneInfo(schedule_tz)
    except Exception:
        business_zone = ZoneInfo("UTC")

    local_start = datetime.combine(target_date, requested_time, tzinfo=business_zone)
    start_dt = local_start.astimezone(timezone.utc)
    end_dt = start_dt + timedelta(minutes=slot_duration)
    now = datetime.now(timezone.utc)
    lock_until = now + timedelta(minutes=lock_minutes)

    try:
        # Check if row already exists
        existing_result = await db.execute(
            select(TimeSlot)
            .where(TimeSlot.org_id == org_id, TimeSlot.start_time == start_dt)
            .with_for_update(nowait=True)
        )
        existing = existing_result.scalars().first()

        if existing:
            if existing.is_booked:
                logger.warning(f"Slot {start_dt} already booked for org {org_id}")
                return None
            if existing.locked_until and existing.locked_until > now:
                logger.warning(f"Slot {start_dt} actively locked for org {org_id}")
                return None
            # Re-use expired lock row
            existing.locked_until = lock_until
            await db.commit()
            return existing

        # Create new lock row
        slot = TimeSlot(
            org_id=org_id,
            start_time=start_dt,
            end_time=end_dt,
            is_booked=False,
            locked_until=lock_until,
        )
        db.add(slot)
        await db.commit()
        await db.refresh(slot)
        logger.info(f"Created lock-only TimeSlot {slot.id} for {start_dt}")
        return slot

    except Exception as e:
        await db.rollback()
        logger.error(f"create_timeslot_lock failed for {start_dt}: {e}")
        return None


def format_slot_time(t: time) -> str:
    """Format a time object as '9:00 AM', '12:30 PM' etc."""
    hour = t.hour
    minute = t.minute
    ampm = "AM" if hour < 12 else "PM"
    hour_12 = hour % 12 or 12
    return f"{hour_12}:{minute:02d} {ampm}"
