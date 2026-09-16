from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.booking import (
    BusinessSchedule,
    ExceptionType,
    ScheduleDay,
    ScheduleException,
)


def _normalize_timezone(tz_name: Optional[str], fallback: str = "UTC") -> str:
    if not tz_name:
        tz_name = fallback
    try:
        return ZoneInfo(tz_name).key
    except Exception:
        return "UTC"


def _ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_effective_timezone_policy(
    requested_tz: Optional[str],
    org_tz: Optional[str],
    requested_source: Optional[str] = None,
) -> dict:
    normalized_org_tz = _normalize_timezone(org_tz, fallback="UTC")
    source: Literal["detected", "user_selected"] = "detected"
    if (requested_source or "").strip().lower() == "user_selected":
        source = "user_selected"

    if not requested_tz:
        return {
            "effective_timezone": normalized_org_tz,
            "timezone_source": "fallback_org",
            "fallback_used": True,
            "fallback_reason": "missing_tz",
        }

    try:
        requested_zone = ZoneInfo(requested_tz)
        return {
            "effective_timezone": requested_zone.key,
            "timezone_source": source,
            "fallback_used": False,
            "fallback_reason": None,
        }
    except Exception:
        return {
            "effective_timezone": normalized_org_tz,
            "timezone_source": "fallback_org",
            "fallback_used": True,
            "fallback_reason": "invalid_tz",
        }


def evaluate_same_day_policy(
    target_date: date,
    now_utc: datetime,
    effective_timezone: str,
    allow_same_day_booking: bool,
) -> dict:
    zone = ZoneInfo(_normalize_timezone(effective_timezone))
    now_local = _ensure_aware_utc(now_utc).astimezone(zone)
    if not allow_same_day_booking and target_date == now_local.date():
        return {"allowed": False, "reason": "same_day_disabled"}
    return {"allowed": True, "reason": None}


def evaluate_slot_cutoff_policy(
    slot_start_utc: datetime,
    now_utc: datetime,
    effective_timezone: str,
    slot_cutoff_minutes: Optional[int],
) -> dict:
    zone = ZoneInfo(_normalize_timezone(effective_timezone))
    effective_cutoff_minutes = (
        5 if slot_cutoff_minutes is None else max(0, int(slot_cutoff_minutes))
    )
    now_local = _ensure_aware_utc(now_utc).astimezone(zone)
    slot_local = _ensure_aware_utc(slot_start_utc).astimezone(zone)

    cutoff_threshold = now_local + timedelta(minutes=effective_cutoff_minutes)
    if slot_local <= cutoff_threshold:
        return {
            "allowed": False,
            "reason": "past_time_cutoff",
            "cutoff_minutes": effective_cutoff_minutes,
        }
    return {
        "allowed": True,
        "reason": None,
        "cutoff_minutes": effective_cutoff_minutes,
    }


SCHEDULE_QUERY_KEYWORDS = (
    "business hours",
    "working hours",
    "opening hours",
    "open",
    "close",
    "hours",
    "schedule",
    "timing",
    "timings",
    "available",
    "availability",
    "slot",
    "slots",
    "bookable",
)

_QUESTION_PREFIXES = (
    "what",
    "when",
    "where",
    "which",
    "who",
    "is",
    "are",
    "do",
    "does",
    "can",
    "could",
    "tell",
    "kya",
    "kab",
    "kitne",
)


def _looks_like_question(user_message: str) -> bool:
    """Heuristic: is this phrased as a question rather than a statement?

    Used to gate the weekly-hours fallback so booking-flow statements like
    "that timing works for me" never hijack the conversation.
    """
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    if "?" in text:
        return True
    return any(text.startswith(prefix + " ") for prefix in _QUESTION_PREFIXES)


def format_weekly_hours_summary(
    weekly_schedule: list[dict[str, Any]],
    timezone_name: str,
    slot_minutes: Optional[int] = None,
    booking_window_days: Optional[int] = None,
) -> str:
    """Render the weekly schedule as a compact human-readable summary.

    Consecutive days with identical hours are grouped ("Monday to Friday").
    Returns "" when no day is active.
    """
    by_weekday: dict[int, dict[str, Any]] = {}
    for item in weekly_schedule or []:
        raw_day = item.get("day_of_week")
        if raw_day is None:
            continue
        try:
            by_weekday[int(raw_day)] = item
        except (TypeError, ValueError):
            continue

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    segments: list[str] = []
    active_count = 0

    weekday = 0
    while weekday < 7:
        day = by_weekday.get(weekday)
        is_active = bool(day and day.get("is_active"))
        open_time = _coerce_time(day.get("open_time")) if day else None
        close_time = _coerce_time(day.get("close_time")) if day else None
        signature = (
            (open_time.hour, open_time.minute, close_time.hour, close_time.minute)
            if is_active and open_time and close_time
            else None
        )

        # Find the run of consecutive days sharing the same hours.
        run_end = weekday
        while run_end + 1 < 7:
            nxt = by_weekday.get(run_end + 1)
            nxt_active = bool(nxt and nxt.get("is_active"))
            nxt_open = _coerce_time(nxt.get("open_time")) if nxt else None
            nxt_close = _coerce_time(nxt.get("close_time")) if nxt else None
            nxt_signature = (
                (nxt_open.hour, nxt_open.minute, nxt_close.hour, nxt_close.minute)
                if nxt_active and nxt_open and nxt_close
                else None
            )
            if nxt_signature != signature:
                break
            run_end += 1

        if signature is None:
            if weekday == run_end:
                segments.append(f"{day_names[weekday]}: Closed")
            else:
                segments.append(f"{day_names[weekday]} to {day_names[run_end]}: Closed")
        elif open_time is not None and close_time is not None:
            active_count += 1
            hours_text = (
                f"{_format_display_time(open_time)} – {_format_display_time(close_time)}"
            )
            if weekday == run_end:
                segments.append(f"{day_names[weekday]}: {hours_text}")
            else:
                segments.append(
                    f"{day_names[weekday]} to {day_names[run_end]}: {hours_text}"
                )
        weekday = run_end + 1

    if active_count == 0:
        return ""

    parts = ["; ".join(segments) + f" (times in {timezone_name})"]
    if slot_minutes:
        parts.append(f"each appointment is {slot_minutes} minutes long")
    if booking_window_days:
        parts.append(f"bookings can be made up to {booking_window_days} days in advance")
    return ". ".join(parts) + "."


async def load_weekly_hours_block(db: Any, org_id: str) -> str:
    """Load the org's schedule and render it as a system-prompt block.

    Returns "" when the org has no schedule configured so callers can skip
    injection entirely.
    """
    if db is None or not hasattr(db, "execute"):
        return ""
    try:
        org_uuid = uuid.UUID(str(org_id))
    except Exception:
        return ""

    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_uuid)
    )
    schedule = schedule_result.scalars().first()
    if not schedule:
        return ""

    day_result = await db.execute(
        select(ScheduleDay).where(ScheduleDay.org_id == org_uuid)
    )
    schedule_days = day_result.scalars().all()
    if not schedule_days:
        return ""

    weekly_schedule = [
        {
            "day_of_week": row.day_of_week,
            "is_active": row.is_active,
            "open_time": row.open_time,
            "close_time": row.close_time,
        }
        for row in schedule_days
    ]
    summary = format_weekly_hours_summary(
        weekly_schedule,
        timezone_name=_normalize_timezone(
            str(getattr(schedule, "timezone", None) or "UTC")
        ),
        slot_minutes=getattr(schedule, "slot_duration_minutes", None),
        booking_window_days=getattr(schedule, "booking_window_days", None),
    )
    if not summary:
        return ""

    return (
        "## BUSINESS HOURS (authoritative — answer timing questions from this)\n"
        f"- {summary}\n"
        "Answer questions about opening hours, timings, availability windows or\n"
        "booking rules directly from this block. Only start the booking flow when\n"
        "the user actually wants to book an appointment."
    )

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def is_schedule_business_hours_query(user_message: str) -> bool:
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in SCHEDULE_QUERY_KEYWORDS)


def _coerce_time(value: Any) -> Optional[time]:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return time.fromisoformat(raw)
    except Exception:
        return None


def _format_display_time(value: time) -> str:
    hour_12 = value.hour % 12 or 12
    ampm = "AM" if value.hour < 12 else "PM"
    return f"{hour_12}:{value.minute:02d} {ampm}"


def _resolve_target_date_from_message(
    user_message: str,
    effective_timezone: str,
    reference_utc: Optional[datetime] = None,
) -> Optional[date]:
    text = str(user_message or "").strip().lower()
    if not text:
        return None

    import re

    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if date_match:
        try:
            return date.fromisoformat(date_match.group(1))
        except ValueError:
            return None

    zone = ZoneInfo(_normalize_timezone(effective_timezone))
    now_utc = _ensure_aware_utc(reference_utc or datetime.now(timezone.utc))
    local_today = now_utc.astimezone(zone).date()

    if "today" in text:
        return local_today
    if "tomorrow" in text:
        return local_today + timedelta(days=1)

    for weekday_name, weekday_index in _WEEKDAY_INDEX.items():
        if weekday_name in text:
            delta = (weekday_index - local_today.weekday()) % 7
            return local_today + timedelta(days=delta)
    return None


def build_schedule_business_hours_answer(
    *,
    user_message: str,
    effective_timezone: str,
    schedule_timezone: str,
    weekly_schedule: list[dict[str, Any]],
    schedule_exceptions: list[dict[str, Any]],
    reference_utc: Optional[datetime] = None,
    target_date: Optional[date] = None,
) -> Optional[dict[str, Any]]:
    if not is_schedule_business_hours_query(user_message):
        return None

    resolved_target_date = target_date or _resolve_target_date_from_message(
        user_message=user_message,
        effective_timezone=effective_timezone,
        reference_utc=reference_utc,
    )
    if resolved_target_date is None:
        return None

    schedule_by_weekday: dict[int, dict[str, Any]] = {}
    for item in weekly_schedule:
        raw_day = item.get("day_of_week")
        if raw_day is None:
            continue
        try:
            schedule_by_weekday[int(raw_day)] = item
        except (TypeError, ValueError):
            continue
    exceptions_by_date: dict[date, dict[str, Any]] = {}
    for item in schedule_exceptions:
        raw_date = item.get("exception_date")
        if isinstance(raw_date, date):
            parsed = raw_date
        else:
            try:
                parsed = date.fromisoformat(str(raw_date))
            except Exception:
                continue
        exceptions_by_date[parsed] = item

    day_name = resolved_target_date.strftime("%A")
    date_text = resolved_target_date.isoformat()
    display_timezone = _normalize_timezone(effective_timezone, fallback=schedule_timezone)

    exception = exceptions_by_date.get(resolved_target_date)
    if exception:
        exception_type = str(exception.get("exception_type") or "").upper()
        if exception_type == ExceptionType.CLOSED.value:
            response_text = f"We're closed on {day_name} ({date_text}) in {display_timezone}."
            return {
                "response_text": response_text,
                "status": "closed_exception",
                "source": "policy",
                "target_date": date_text,
                "effective_timezone": display_timezone,
            }
        if exception_type == ExceptionType.CUSTOM_HOURS.value:
            custom_open = _coerce_time(exception.get("custom_open"))
            custom_close = _coerce_time(exception.get("custom_close"))
            if custom_open and custom_close:
                response_text = (
                    f"Our hours on {day_name} ({date_text}) are "
                    f"{_format_display_time(custom_open)} to {_format_display_time(custom_close)} "
                    f"({display_timezone})."
                )
                return {
                    "response_text": response_text,
                    "status": "open_exception",
                    "source": "policy",
                    "target_date": date_text,
                    "effective_timezone": display_timezone,
                }

    day_policy = schedule_by_weekday.get(resolved_target_date.weekday())
    if not day_policy or not bool(day_policy.get("is_active", False)):
        response_text = f"We're closed on {day_name} ({date_text}) in {display_timezone}."
        return {
            "response_text": response_text,
            "status": "closed",
            "source": "policy",
            "target_date": date_text,
            "effective_timezone": display_timezone,
        }

    open_time = _coerce_time(day_policy.get("open_time"))
    close_time = _coerce_time(day_policy.get("close_time"))
    if not open_time or not close_time:
        return {
            "response_text": f"I couldn't resolve policy hours for {day_name} ({date_text}) yet.",
            "status": "policy_unavailable",
            "source": "policy",
            "target_date": date_text,
            "effective_timezone": display_timezone,
        }

    response_text = (
        f"Our hours on {day_name} ({date_text}) are "
        f"{_format_display_time(open_time)} to {_format_display_time(close_time)} "
        f"({display_timezone})."
    )
    return {
        "response_text": response_text,
        "status": "open",
        "source": "policy",
        "target_date": date_text,
        "effective_timezone": display_timezone,
    }


async def lookup_schedule_business_hours_answer(
    *,
    db: Any,
    org_id: str,
    user_message: str,
    effective_timezone: str,
    org_timezone: Optional[str] = None,
    reference_utc: Optional[datetime] = None,
    in_booking_flow: bool = False,
) -> Optional[dict[str, Any]]:
    if not is_schedule_business_hours_query(user_message):
        return None
    if db is None or not hasattr(db, "execute"):
        return None

    target_date = _resolve_target_date_from_message(
        user_message=user_message,
        effective_timezone=effective_timezone,
        reference_utc=reference_utc,
    )

    try:
        org_uuid = uuid.UUID(str(org_id))
    except Exception:
        return None

    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_uuid)
    )
    schedule = schedule_result.scalars().first()

    # Generic timing questions ("what are your timings?") carry no date —
    # answer with the full weekly summary instead of falling through to the
    # LLM. Gated on question phrasing so booking-flow statements like
    # "that timing works" never hijack the conversation. For WhatsApp
    # parity, we answer even mid-booking-flow (with re-ask of current field)
    # so the user sees hours as per message flow.
    if target_date is None:
        if not _looks_like_question(user_message):
            return None
        if not schedule:
            return {
                "response_text": "I don't have business-hours policy configured yet.",
                "status": "policy_missing",
                "source": "policy",
                "effective_timezone": _normalize_timezone(effective_timezone),
            }
        day_result = await db.execute(
            select(ScheduleDay).where(ScheduleDay.org_id == org_uuid)
        )
        schedule_days = day_result.scalars().all()
        summary = format_weekly_hours_summary(
            [
                {
                    "day_of_week": row.day_of_week,
                    "is_active": row.is_active,
                    "open_time": row.open_time,
                    "close_time": row.close_time,
                }
                for row in schedule_days
            ],
            timezone_name=_normalize_timezone(
                str(getattr(schedule, "timezone", None) or org_timezone or "UTC")
            ),
            slot_minutes=getattr(schedule, "slot_duration_minutes", None),
            booking_window_days=getattr(schedule, "booking_window_days", None),
        )
        if not summary:
            return {
                "response_text": "I don't have business-hours policy configured yet.",
                "status": "policy_missing",
                "source": "policy",
                "effective_timezone": _normalize_timezone(effective_timezone),
            }
        return {
            "response_text": f"Here are our timings: {summary}",
            "status": "weekly_hours",
            "source": "policy",
            "effective_timezone": _normalize_timezone(effective_timezone),
        }

    if not schedule:
        return {
            "response_text": "I don't have business-hours policy configured yet.",
            "status": "policy_missing",
            "source": "policy",
            "target_date": target_date.isoformat(),
            "effective_timezone": _normalize_timezone(effective_timezone),
        }

    day_result = await db.execute(
        select(ScheduleDay).where(ScheduleDay.org_id == org_uuid)
    )
    schedule_days = day_result.scalars().all()
    exc_result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.org_id == org_uuid,
            ScheduleException.exception_date == target_date,
        )
    )
    schedule_exception = exc_result.scalars().first()

    weekly_schedule = [
        {
            "day_of_week": row.day_of_week,
            "is_active": row.is_active,
            "open_time": row.open_time,
            "close_time": row.close_time,
        }
        for row in schedule_days
    ]
    exceptions = []
    if schedule_exception:
        exceptions.append(
            {
                "exception_date": schedule_exception.exception_date,
                "exception_type": str(schedule_exception.exception_type),
                "custom_open": schedule_exception.custom_open,
                "custom_close": schedule_exception.custom_close,
            }
        )

    return build_schedule_business_hours_answer(
        user_message=user_message,
        effective_timezone=effective_timezone,
        schedule_timezone=str(getattr(schedule, "timezone", None) or org_timezone or "UTC"),
        weekly_schedule=weekly_schedule,
        schedule_exceptions=exceptions,
        reference_utc=reference_utc,
        target_date=target_date,
    )
