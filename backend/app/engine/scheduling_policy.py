from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional, Sequence

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
import logging as _logging

_logger = _logging.getLogger(__name__)

#: Absolute bounds for any booking horizon. The ceiling is leap-aware: a
#: year has 365 days, 366 in a leap year — so 366 is allowed only when the
#: upcoming booking window can actually contain a Feb 29 (see below).
MIN_HORIZON_DAYS = 1
ABS_MAX_HORIZON_DAYS = 366


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def max_horizon_days(today: Optional[date] = None) -> int:
    """Leap-aware horizon ceiling for a window starting `today`.

    Returns 366 only when a Feb 29 falls inside the next 366 days (i.e. a
    leap year's February is reachable); otherwise 365. E.g. during 2026
    (non-leap, next leap 2028 out of reach until Dec 2027) the cap is 365.
    """
    today = today or date.today()
    horizon_end = today + timedelta(days=ABS_MAX_HORIZON_DAYS)
    year = today.year
    while year <= horizon_end.year:
        if _is_leap_year(year):
            feb29 = date(year, 2, 29)
            if today <= feb29 <= horizon_end:
                return ABS_MAX_HORIZON_DAYS
        year += 1
    return 365


def clamp_horizon_days(raw: Any, today: Optional[date] = None) -> int:
    """Clamp any horizon input into [1, leap-aware max]; unparseable -> 60."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 60
    return max(MIN_HORIZON_DAYS, min(max_horizon_days(today), value))


async def resolve_booking_horizon_days(
    db: Any, org_id: Any, org_config: Any = None
) -> tuple[int, str, bool]:
    """Single source of truth for the booking horizon (in days).

    Precedence (availability wins — prompts must never promise dates with
    no slots): BusinessSchedule.booking_window_days, then
    OrgConfig.max_booking_days_ahead, then 60. Everything is clamped to
    [1, 366]. Returns (days, source, diverged) where diverged=True means
    the two stored values disagreed (dashboard sync should prevent this).
    """
    sched_window: Any = None
    cfg_window: Any = getattr(org_config, "max_booking_days_ahead", None)
    try:
        _sched = (
            await db.execute(
                select(BusinessSchedule).where(BusinessSchedule.org_id == org_id)
            )
        ).scalars().first()
        if _sched is not None:
            sched_window = getattr(_sched, "booking_window_days", None)
    except Exception:
        _logger.warning("horizon schedule lookup failed", exc_info=True)
    valid_sched = (
        isinstance(sched_window, int) and not isinstance(sched_window, bool) and sched_window > 0
    )
    valid_cfg = (
        isinstance(cfg_window, int) and not isinstance(cfg_window, bool) and cfg_window > 0
    )
    diverged = bool(valid_sched and valid_cfg and sched_window != cfg_window)
    if diverged:
        _logger.warning(
            "booking horizon divergence: schedule=%s config=%s; using schedule",
            sched_window,
            cfg_window,
        )
    if valid_sched:
        return clamp_horizon_days(sched_window), "schedule", diverged
    if valid_cfg:
        _logger.warning("booking horizon falling back to org_config value")
        return clamp_horizon_days(cfg_window), "org_config", diverged
    _logger.warning("booking horizon missing everywhere; using default 60")
    return 60, "default", diverged


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
        select(ScheduleDay).where(
            ScheduleDay.org_id == org_uuid,
            ScheduleDay.provider_id.is_(None),
        )
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
    # No in_booking_flow gate: hours questions mid-booking ("What are your
    # working hours?" with a name already collected) must be answered from
    # the weekly schedule, not declined. Both callers (graph early-return
    # and legacy answer+re-ask) preserve booking progress; only the
    # question phrasing gates (statements like "that timing works" never
    # match). Production: the question fell through to RAG and the customer
    # got "I don't have information about that" for hours we configure.
    # in_booking_flow stays in the signature so callers need no changes.
    _ = in_booking_flow
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
        # Anaphoric timing references ("is that timing okay?", "that timing
        # works") ask about a SPECIFIC proposed time, not general hours —
        # they fall through so the slot/time logic judges the actual time.
        # Bare general-hours phrasing ("what are your working hours") gets
        # the weekly table. Pure phrasing shapes, never topic judgments.
        _hours_text_l = str(user_message or "").strip().lower()
        _anaphoric_timing = bool(
            re.search(r"\b(that|this|it)\b", _hours_text_l)
        ) and not any(
            _general in _hours_text_l
            for _general in (
                "working hours",
                "business hours",
                "office hours",
                "opening hours",
                "open hours",
                "hours of operation",
                "timings",
                "when are you open",
                "when do you open",
            )
        )
        if _anaphoric_timing:
            return None
        if not schedule:
            return {
                "response_text": "I don't have business-hours policy configured yet.",
                "status": "policy_missing",
                "source": "policy",
                "effective_timezone": _normalize_timezone(effective_timezone),
            }
        day_result = await db.execute(
            select(ScheduleDay).where(
                ScheduleDay.org_id == org_uuid,
                ScheduleDay.provider_id.is_(None),
            )
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
        select(ScheduleDay).where(
            ScheduleDay.org_id == org_uuid,
            ScheduleDay.provider_id.is_(None),
        )
    )
    schedule_days = day_result.scalars().all()
    exc_result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.org_id == org_uuid,
            ScheduleException.exception_date == target_date,
            ScheduleException.provider_id.is_(None),
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


# ── Available-dates inquiries ("which dates can I book?") ─────────────────
# Sibling of the hours-question path above: detects questions about *which
# dates* have availability (not business hours), then answers from the live
# slot inventory — never from static labels. Without this, such questions
# fall through to field collection and the user is interrogated instead
# of answered (e.g. asked for their phone number twice).

AVAILABLE_DATES_QUERY_PATTERNS = (
    "available date",
    "available dates",
    "availability of dates",
    "date availability",
    "which date",
    "which dates",
    "what dates",
    "what date",
    "when can i book",
    "when do you have",
    "slots available",
    "slot available",
    "any slots",
    "any slot",
    "open dates",
    "free dates",
    "days available",
)

# Concrete date references: if the message names an actual date, it is a
# booking statement/value — not an availability inquiry. Handled by the
# normal date path (typed entry + validation).
_DATE_VALUE_PATTERNS = (
    r"\b\d{4}-\d{2}-\d{2}\b",  # ISO
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",  # dd/mm/yyyy
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    r"\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    r"\btoday\b",
    r"\btomorrow\b",
)

_INQUIRY_LEAD_PATTERNS = (
    "tell me",
    "show me",
    "let me know",
    "give me",
    "please",
)


def _contains_concrete_date_value(text_lower: str) -> bool:
    import re as _re

    return any(_re.search(pat, text_lower) for pat in _DATE_VALUE_PATTERNS)


def is_available_dates_query(user_message: str) -> bool:
    """True when the user asks *which dates* are bookable.

    Requires an availability pattern plus question phrasing, and excludes
    messages naming a concrete date (those are booking values, handled by
    typed-date entry + validation — never swallowed here).
    """
    text = str(user_message or "").strip().lower()
    if not text:
        return False
    if _contains_concrete_date_value(text):
        return False
    if not any(pat in text for pat in AVAILABLE_DATES_QUERY_PATTERNS):
        return False
    if "?" in text:
        return True
    if any(text.startswith(prefix + " ") for prefix in _QUESTION_PREFIXES):
        return True
    return any(lead in text for lead in _INQUIRY_LEAD_PATTERNS)


async def lookup_available_dates(
    *,
    db: Any,
    org_id: str,
    effective_timezone: str,
    org_timezone: Optional[str] = None,
    max_dates: int = 7,
    scan_cap_days: int = 21,
) -> Optional[dict[str, Any]]:
    """Return the next bookable days from the live slot inventory.

    Deliberately has NO question gate. The date step needs this list even
    when the customer never asked for it — "What date would you prefer?" is
    unanswerable without knowing what is open. Callers that DO want the
    "which dates can I book?" behaviour should use
    `lookup_available_dates_answer`, which gates on the question first.

    Returns None when availability cannot be determined at all (no usable
    db handle, malformed org id, schedule row unreadable) so callers can fall
    back rather than telling the customer there are no dates.
    """
    if db is None or not hasattr(db, "execute"):
        return None

    from app.engine.slot_engine import compute_slot_inventory

    try:
        org_uuid = uuid.UUID(str(org_id))
    except Exception:
        return None

    biz_tz = _normalize_timezone(org_timezone or effective_timezone)
    try:
        horizon_days, _, _ = await resolve_booking_horizon_days(db, org_uuid, None)
    except Exception:
        horizon_days = 60

    try:
        zone = ZoneInfo(biz_tz)
    except Exception:
        zone = ZoneInfo("UTC")
    now_utc = datetime.now(timezone.utc)
    today = now_utc.astimezone(zone).date()

    from app.engine.slot_engine import apply_scheduling_policy_to_inventory

    try:
        sched_row = (
            await db.execute(
                select(BusinessSchedule).where(BusinessSchedule.org_id == org_uuid)
            )
        ).scalars().first()
    except Exception:
        return None
    if not sched_row:
        return {
            "response_text": "I don't have booking availability configured yet.",
            "status": "policy_missing",
            "source": "availability",
            "available_dates": [],
            "effective_timezone": biz_tz,
        }
    allow_same_day = bool(getattr(sched_row, "allow_same_day_booking", False))
    cutoff_minutes = getattr(sched_row, "slot_cutoff_minutes", 5)

    found: list[date] = []
    scan_days = max(1, min(int(horizon_days or 60), int(scan_cap_days)))
    for offset in range(scan_days):
        day = today + timedelta(days=offset)
        try:
            inventory, _status = await compute_slot_inventory(
                db, org_uuid, day, org_config=None
            )
            inventory, _status, _reason = apply_scheduling_policy_to_inventory(
                inventory,
                day,
                base_status=_status,
                effective_timezone=biz_tz,
                allow_same_day_booking=allow_same_day,
                slot_cutoff_minutes=cutoff_minutes,
                now_utc=now_utc,
            )
        except Exception:
            continue
        if any(item.get("available") for item in (inventory or [])):
            found.append(day)
        if len(found) >= max_dates:
            break

    if not found:
        return {
            "response_text": (
                f"No open dates in the next {scan_days} days — "
                "please try a later date or contact us directly."
            ),
            "status": "none_found",
            "source": "availability",
            "available_dates": [],
            "effective_timezone": biz_tz,
        }
    labels = [d.strftime("%a, %b %d") for d in found]
    # Horizon wording (never a day menu on WhatsApp): the customer-facing
    # text is the booking window, while the slot-checked day labels stay in
    # the payload for transports that render their own pickers.
    try:
        from app.engine.message_processor import (
            _format_available_dates_for_prompt as _horizon_text,
        )
        from app.engine.message_processor import (
            fetch_closed_weekdays as _closed_days,
        )

        try:
            _closed = await _closed_days(db, org_uuid)
        except Exception:
            _closed = []
        _response_text = _horizon_text(
            biz_tz, int(horizon_days or 60), closed_weekdays=_closed
        )
    except Exception:
        _response_text = (
            "Here are our next available dates: "
            + " · ".join(labels)
            + f". (All dates in {biz_tz}.)"
        )
    if not str(_response_text or "").strip():
        _response_text = (
            "Here are our next available dates: "
            + " · ".join(labels)
            + f". (All dates in {biz_tz}.)"
        )
    return {
        "response_text": _response_text,
        "status": "ok",
        "source": "availability",
        "available_dates": [d.isoformat() for d in found],
        # Same labels the customer is meant to tap/retype. The WhatsApp
        # transports render these inline in the date step instead of sending
        # the whole response_text as a separate message.
        "available_date_labels": labels,
        "effective_timezone": biz_tz,
    }


async def select_date_option_labels(
    *,
    db: Any,
    org_id: str,
    org_timezone: Optional[str] = None,
    directive_options: Optional[Sequence[Any]] = None,
    max_dates: int = 7,
) -> list[str]:
    """Labels to show the customer at the SELECT_DATE step.

    The stored directive options are plain upcoming calendar days, so they can
    include days the business is closed or fully booked. Showing those would
    send the customer to a day that cannot be booked, so the live scan wins
    whenever it can answer. The directive options are the fallback so a
    database hiccup degrades to the old behaviour instead of silence.
    """
    fallback = [
        str(o).strip()
        for o in (directive_options or [])
        if isinstance(o, str) and str(o).strip()
    ]
    try:
        answer = await lookup_available_dates(
            db=db,
            org_id=org_id,
            effective_timezone=str(org_timezone or "UTC"),
            org_timezone=str(org_timezone or "UTC"),
            max_dates=max_dates,
        )
    except Exception:
        return fallback[:max_dates]
    if not answer or str(answer.get("status") or "") != "ok":
        return fallback[:max_dates]
    labels = [str(x).strip() for x in (answer.get("available_date_labels") or [])]
    return (labels or fallback)[:max_dates]


async def lookup_available_dates_answer(
    *,
    db: Any,
    org_id: str,
    user_message: str,
    effective_timezone: str,
    org_timezone: Optional[str] = None,
    max_dates: int = 7,
    scan_cap_days: int = 21,
) -> Optional[dict[str, Any]]:
    """Answer 'which dates can I book?' from the live slot inventory.

    Returns None when the message is not an availability inquiry. Otherwise
    scans upcoming days (horizon from the single-source resolver) and keeps
    only days with at least one genuinely free slot — closed days, CLOSED
    exceptions, past-cutoff and fully-booked days are all excluded. External
    calendar busy-periods are intentionally not consulted here (bounded,
    DB-local scan); the time-step path still enforces them per date.
    """
    if not is_available_dates_query(user_message):
        return None
    return await lookup_available_dates(
        db=db,
        org_id=org_id,
        effective_timezone=effective_timezone,
        org_timezone=org_timezone,
        max_dates=max_dates,
        scan_cap_days=scan_cap_days,
    )
