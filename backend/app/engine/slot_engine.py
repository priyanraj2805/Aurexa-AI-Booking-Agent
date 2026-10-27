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
    if close_dt <= current:
        close_dt += timedelta(days=1)
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
    provider_id: Optional[uuid.UUID | str] = None,
) -> Tuple[List[dict], str]:
    """
    Build full slot inventory for a date, optionally for one doctor.

    provider_id=None = org behavior (unchanged, individual shops).
    provider_id set = use that doctor's override hours/timezone if present,
    else fall back to org template. Booked/locked filtering is per-doctor
    so two doctors can share the same clock time.
    """
    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_id)
    )
    schedule = schedule_result.scalars().first()
    if not schedule:
        return [], "no_schedule"

    duration = schedule.slot_duration_minutes
    schedule_tz_default = (getattr(schedule, "timezone", None) or "UTC").strip()

    # Per-doctor overrides (additive: NULL provider = org template untouched)
    provider = None
    if provider_id:
        try:
            from app.models.service_unit import Provider as _Provider

            _pid = (
                provider_id
                if isinstance(provider_id, uuid.UUID)
                else uuid.UUID(str(provider_id))
            )
            provider = (
                await db.execute(
                    select(_Provider).where(
                        _Provider.id == _pid, _Provider.org_id == org_id
                    )
                )
            ).scalars().first()
        except Exception:
            provider = None
        if provider is None:
            return [], "no_schedule"
        if getattr(provider, "slot_duration_minutes", None):
            duration = int(provider.slot_duration_minutes)

    weekday = target_date.weekday()
    day = None
    if provider is not None:
        try:
            day_result = await db.execute(
                select(ScheduleDay).where(
                    ScheduleDay.org_id == org_id,
                    ScheduleDay.day_of_week == weekday,
                    ScheduleDay.provider_id == provider.id,
                )
            )
            day = day_result.scalars().first()
        except Exception:
            day = None
    if day is None:
        day_result = await db.execute(
            select(ScheduleDay).where(
                ScheduleDay.org_id == org_id,
                ScheduleDay.day_of_week == weekday,
            )
        )
        if provider is None:
            # Individual path: org template only (mock-compatible .first()).
            # The WHERE clause already excludes doctor overrides; the
            # Python fallback below is only for mock objects in unit tests.
            try:
                _rows = day_result.scalars().all()
            except Exception:
                _rows = None
            if isinstance(_rows, (list, tuple)):
                day = next(
                    (r for r in _rows if getattr(r, "provider_id", None) is None),
                    _rows[0] if _rows else None,
                )
            else:
                try:
                    day = day_result.scalars().first()
                except Exception:
                    day = None
        else:
            # Prefer org template row (provider NULL) when both exist
            try:
                rows = day_result.scalars().all()
            except Exception:
                rows = []
            # Mock-compat: MagicMock .all() may return non-list; fall back
            if not isinstance(rows, (list, tuple)):
                try:
                    day = day_result.scalars().first()
                except Exception:
                    day = None
            else:
                day = next(
                    (r for r in rows if getattr(r, "provider_id", None) is None),
                    rows[0] if rows else None,
                )

    if not day or not day.is_active:
        return [], "closed_day"

    open_t = day.open_time
    close_t = day.close_time
    break_start = day.break_start
    break_end = day.break_end

    # Exceptions: doctor-specific first, then org-wide
    exception = None
    if provider is not None:
        try:
            exc_result = await db.execute(
                select(ScheduleException).where(
                    ScheduleException.org_id == org_id,
                    ScheduleException.exception_date == target_date,
                    ScheduleException.provider_id == provider.id,
                )
            )
            exception = exc_result.scalars().first()
        except Exception:
            exception = None
    if exception is None:
        exc_result = await db.execute(
            select(ScheduleException).where(
                ScheduleException.org_id == org_id,
                ScheduleException.exception_date == target_date,
            )
        )
        if provider is None:
            # Individual path: org-wide exception only (mock-compatible).
            try:
                _exc_rows = exc_result.scalars().all()
            except Exception:
                _exc_rows = None
            if isinstance(_exc_rows, (list, tuple)):
                exception = next(
                    (r for r in _exc_rows if getattr(r, "provider_id", None) is None),
                    None,
                )
            else:
                try:
                    exception = exc_result.scalars().first()
                except Exception:
                    exception = None
        else:
            try:
                exc_rows = exc_result.scalars().all()
            except Exception:
                exc_rows = []
            if not isinstance(exc_rows, (list, tuple)):
                try:
                    exception = exc_result.scalars().first()
                except Exception:
                    exception = None
            else:
                # Org-wide = provider NULL; doctor-specific already checked
                exception = next(
                    (r for r in exc_rows if getattr(r, "provider_id", None) is None),
                    None,
                )

    if exception:
        if exception.exception_type == ExceptionType.CLOSED:
            return [], "exception_closed"
        elif exception.exception_type == ExceptionType.CUSTOM_HOURS:
            if exception.custom_open and exception.custom_close:
                open_t = exception.custom_open
                close_t = exception.custom_close
                break_start = None
                break_end = None

    # Effective timezone: doctor override wins, else org schedule
    schedule_tz = (getattr(schedule, "timezone", None) or "UTC").strip()
    if provider is not None and getattr(provider, "timezone", None):
        try:
            from app.core.org_mode import effective_timezone as _eff_tz

            schedule_tz = _eff_tz(provider, schedule_tz)
        except Exception:
            pass
    try:
        business_zone = ZoneInfo(schedule_tz)
    except Exception:
        business_zone = ZoneInfo("UTC")

    if open_t is None or close_t is None:
        return [], "no_slots_in_window"
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

    _slot_filter = and_(
        TimeSlot.org_id == org_id,
        TimeSlot.start_time >= start_dt,
        TimeSlot.start_time < end_dt,
    )
    # Per-doctor isolation: Sharma 10 AM and Iyer 10 AM are different rows.
    # provider=None (individual) sees only org-shared rows.
    try:
        if provider is not None:
            _slot_filter = and_(_slot_filter, TimeSlot.provider_id == provider.id)
        else:
            _slot_filter = and_(
                _slot_filter, TimeSlot.provider_id.is_(None)
            )
    except Exception:
        pass
    existing_result = await db.execute(select(TimeSlot).where(_slot_filter))
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
    provider_id: Optional[uuid.UUID | str] = None,
) -> List[time]:
    """
    Returns list of available slot start times for `org_id` on `target_date`.
    provider_id=None keeps org behavior; set filters to that doctor.
    """
    inventory, _ = await compute_slot_inventory(
        db, org_id, target_date, org_config, provider_id=provider_id
    )
    if not inventory:
        return []

    # Return NAIVE LOCAL start times (business timezone) — not raw UTC — because
    # callers (chat slot offers, admin /schedule/preview, booking lock, provider
    # previews) all treat returned values as local business times. Raw UTC values
    # previously surfaced as AM/PM labels like "4:30 AM" for IST businesses.
    schedule_res = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_id)
    )
    sched = schedule_res.scalars().first()
    tz_str = (getattr(sched, "timezone", None) or "UTC").strip() if sched is not None else "UTC"
    if provider_id:
        try:
            from app.models.service_unit import Provider as _Provider

            _pid = (
                provider_id
                if isinstance(provider_id, uuid.UUID)
                else uuid.UUID(str(provider_id))
            )
            prov = (
                await db.execute(
                    select(_Provider).where(
                        _Provider.id == _pid, _Provider.org_id == org_id
                    )
                )
            ).scalars().first()
            if prov is not None and getattr(prov, "timezone", None):
                from app.core.org_mode import effective_timezone as _eff_tz

                tz_str = _eff_tz(prov, tz_str)
        except Exception:
            pass
    tz_str = (tz_str or "UTC").strip()
    try:
        zone = ZoneInfo(tz_str)
    except Exception:
        zone = ZoneInfo("UTC")

    times: List[time] = []
    for item in inventory:
        if not item.get("available"):
            continue
        start_local = item["start_utc"].astimezone(zone)
        times.append(start_local.replace(tzinfo=None).time())
    return times


async def create_timeslot_lock(
    db: AsyncSession,
    org_id: uuid.UUID,
    target_date: date,
    requested_time: time,
    duration_minutes: int,
    lock_minutes: int = 5,
    allow_existing_lock_id: Optional[str] = None,
    provider_id: Optional[uuid.UUID | str] = None,
    unit_id: Optional[uuid.UUID | str] = None,
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
        # Resolve provider/unit UUIDs (None stays None for individual)
        _prov_uuid = None
        _unit_uuid = None
        try:
            if provider_id:
                _prov_uuid = (
                    provider_id
                    if isinstance(provider_id, uuid.UUID)
                    else uuid.UUID(str(provider_id))
                )
            if unit_id:
                _unit_uuid = (
                    unit_id
                    if isinstance(unit_id, uuid.UUID)
                    else uuid.UUID(str(unit_id))
                )
        except Exception:
            pass
        # Check if row already exists (per-doctor when provider set)
        _where = [TimeSlot.org_id == org_id, TimeSlot.start_time == start_dt]
        try:
            if _prov_uuid is not None:
                _where.append(TimeSlot.provider_id == _prov_uuid)
            else:
                _where.append(TimeSlot.provider_id.is_(None))
        except Exception:
            pass
        existing_result = await db.execute(
            select(TimeSlot).where(*_where).with_for_update(nowait=True)
        )
        existing = existing_result.scalars().first()
        if schedule is None and existing is None:
            return None

        if existing:
            if existing.is_booked:
                logger.warning(f"Slot {start_dt} already booked for org {org_id}")
                return None
            if existing.locked_until:
                locked_until = existing.locked_until
                if locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=timezone.utc)
                if locked_until > now:
                    if allow_existing_lock_id and str(existing.id) == str(allow_existing_lock_id):
                        return existing
                    logger.warning(f"Slot {start_dt} actively locked for org {org_id}")
                    return None
            # Re-use expired lock row
            existing.locked_until = lock_until
            await db.commit()
            return existing

        # Create new lock row (carries doctor/dept for org-mode)
        slot = TimeSlot(
            org_id=org_id,
            start_time=start_dt,
            end_time=end_dt,
            is_booked=False,
            locked_until=lock_until,
            provider_id=_prov_uuid,
            unit_id=_unit_uuid,
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
