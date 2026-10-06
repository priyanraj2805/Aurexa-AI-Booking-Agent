"""Available-dates inquiries: detect the question, answer from live inventory.

Regression: "tell me the available date for booking" used to fall through
to field collection and the user was asked for their phone number instead
of receiving an answer. Detection is pure; the answer builder runs against
a seeded schedule (closed day, CLOSED exception, fully-booked day) and must
list only genuinely bookable dates.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.engine.scheduling_policy import (
    is_available_dates_query,
    lookup_available_dates_answer,
)
from app.models.booking import (
    BusinessSchedule,
    ExceptionType,
    ScheduleDay,
    ScheduleException,
    TimeSlot,
)
from app.models.organization import Organization


def _next_weekday(from_date: date, weekday: int) -> date:
    delta = (weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=delta)


class TestDetector:
    @pytest.mark.parametrize(
        "text",
        [
            "Okay first tell me the available date for booking",
            "Please tell me available date so that i can book appointment",
            "Which dates are available?",
            "What dates do you have?",
            "When can I book?",
            "Are there any slots available?",
            "Tell me open dates",
        ],
    )
    def test_positives(self, text):
        assert is_available_dates_query(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "19 Sep",
            "2026-09-19",
            "tomorrow",
            "Monday",
            "confirm",
            "2",
            "3 PM",
            "that timing works",
            "My phone is 9876543210",
            "I want to book",
            "what are your timings?",
            "",
        ],
    )
    def test_negatives(self, text):
        assert is_available_dates_query(text) is False


@pytest.mark.asyncio
async def test_answer_lists_only_bookable_dates(db_session):
    org = Organization(name="Dates Test Org", api_key="ds_dates_test_key")
    db_session.add(org)
    await db_session.commit()

    today = datetime.now(timezone.utc).date()
    sched = BusinessSchedule(
        org_id=org.id,
        slot_duration_minutes=60,
        booking_window_days=30,
        timezone="UTC",
    )
    db_session.add(sched)
    closed_day = _next_weekday(today, 0)  # next Monday: closed
    exc_day = _next_weekday(today, 2)  # next Wednesday: CLOSED exception
    full_day = _next_weekday(today, 1)  # next Tuesday: fully booked
    # Ensure distinct, non-today days for determinism
    if closed_day <= today:
        closed_day += timedelta(days=7)
    if exc_day <= today:
        exc_day += timedelta(days=7)
    if full_day <= today:
        full_day += timedelta(days=7)
    for dow in range(7):
        db_session.add(
            ScheduleDay(
                org_id=org.id,
                day_of_week=dow,
                is_active=(dow != closed_day.weekday()),
                open_time=time(9, 0),
                close_time=time(17, 0),
            )
        )
    db_session.add(
        ScheduleException(
            org_id=org.id,
            exception_date=exc_day,
            exception_type=ExceptionType.CLOSED,
        )
    )
    # Fully book every hour of full_day (UTC day boundaries)
    for hour in range(24):
        start = datetime(
            full_day.year, full_day.month, full_day.day, hour, tzinfo=timezone.utc
        )
        db_session.add(
            TimeSlot(
                org_id=org.id,
                start_time=start,
                end_time=start + timedelta(minutes=60),
                is_booked=True,
            )
        )
    await db_session.commit()

    answer = await lookup_available_dates_answer(
        db=db_session,
        org_id=str(org.id),
        user_message="Please tell me available date so that i can book appointment",
        effective_timezone="UTC",
        org_timezone="UTC",
    )
    assert answer is not None
    assert answer["source"] == "availability"
    assert answer["status"] == "ok"
    assert len(answer["available_dates"]) >= 1
    assert len(answer["available_dates"]) <= 7
    assert closed_day.isoformat() not in answer["available_dates"]
    assert exc_day.isoformat() not in answer["available_dates"]
    assert full_day.isoformat() not in answer["available_dates"]
    assert "UTC" in answer["response_text"]


@pytest.mark.asyncio
def test_relative_day_words_resolve_to_iso() -> None:
    """Production loop: "Tomorrow" never parsed, so the date stayed pinned
    to a slot-less day and the bot repeated "No available slots" forever.
    Full-string match only — sentences and absolute dates pass through."""
    from datetime import timedelta as _timedelta
    from zoneinfo import ZoneInfo as _ZoneInfo

    from app.engine.validators import DataValidator, _resolve_relative_date

    # Timezone-consistent: the resolver computes in the passed tz, so the
    # expectation must use the same tz — naive date.today() (host clock)
    # disagrees near midnight across zones and flakes the test.
    utc_today = datetime.now(_ZoneInfo("UTC")).date()
    assert _resolve_relative_date("tomorrow", "UTC") == (
        utc_today + _timedelta(days=1)
    ).isoformat()
    assert _resolve_relative_date("  Tomorrow  ", "UTC") is not None
    assert _resolve_relative_date("2026-10-06", "UTC") is None
    assert _resolve_relative_date("book for tomorrow please", "UTC") is None
    assert _resolve_relative_date("", "UTC") is None
    valid, err = DataValidator._validate_date("tomorrow", "UTC", 90)
    assert err is None and valid


def test_horizon_names_closed_days() -> None:
    """The date prompt must say which days are shut, so customers don't
    discover it by trial and error ("We are closed on Sunday.")."""
    from app.engine.message_processor import _format_available_dates_for_prompt

    out = _format_available_dates_for_prompt("UTC", 60, closed_weekdays=[6])
    assert "We are closed on Sunday." in out
    assert "within this horizon" in out
    plain = _format_available_dates_for_prompt("UTC", 60, closed_weekdays=[])
    assert "closed on" not in plain


async def test_answer_non_inquiry_returns_none(db_session):
    answer = await lookup_available_dates_answer(
        db=db_session,
        org_id="00000000-0000-0000-0000-000000000000",
        user_message="19 Sep",
        effective_timezone="UTC",
    )
    assert answer is None


@pytest.mark.asyncio
async def test_answer_no_schedule_policy_missing(db_session):
    org = Organization(name="No Sched Org", api_key="ds_no_sched_key")
    db_session.add(org)
    await db_session.commit()
    answer = await lookup_available_dates_answer(
        db=db_session,
        org_id=str(org.id),
        user_message="Which dates are available?",
        effective_timezone="UTC",
        org_timezone="UTC",
    )
    assert answer is not None
    assert answer["status"] == "policy_missing"
    assert answer["available_dates"] == []
