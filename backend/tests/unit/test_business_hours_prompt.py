"""Tests for weekly business-hours grounding in the chat agent.

Covers:
- ``format_weekly_hours_summary`` day grouping / closed days / extras
- ``load_weekly_hours_block`` prompt block generation
- ``_looks_like_question`` gating for the weekly fallback
- ``lookup_schedule_business_hours_answer`` weekly-summary path (no date)
  and unchanged specific-day path (with date)
- ``build_system_prompt`` injecting the hours block
"""

from datetime import date, time

import pytest

from app.engine.scheduling_policy import (
    _looks_like_question,
    format_weekly_hours_summary,
    is_schedule_business_hours_query,
    load_weekly_hours_block,
    lookup_schedule_business_hours_answer,
)
from app.engine.agent.prompts import build_system_prompt


def _week(mon_to_fri=(time(9, 0), time(17, 0)), sat=None, sun=None):
    """Build a 7-day weekly schedule list. sat/sun are (open, close) or None=closed."""
    days = []
    for idx in range(7):
        if idx < 5:
            hours = mon_to_fri
        elif idx == 5:
            hours = sat
        else:
            hours = sun
        if hours:
            days.append(
                {
                    "day_of_week": idx,
                    "is_active": True,
                    "open_time": hours[0],
                    "close_time": hours[1],
                }
            )
        else:
            days.append(
                {
                    "day_of_week": idx,
                    "is_active": False,
                    "open_time": None,
                    "close_time": None,
                }
            )
    return days


# ── format_weekly_hours_summary ──────────────────────────────────────────


def test_summary_groups_consecutive_identical_days():
    text = format_weekly_hours_summary(_week(), "Asia/Kolkata")
    assert "Monday to Friday: 9:00 AM – 5:00 PM" in text
    assert "Saturday to Sunday: Closed" in text
    assert "(times in Asia/Kolkata)" in text


def test_summary_includes_slot_and_window():
    text = format_weekly_hours_summary(
        _week(), "Asia/Kolkata", slot_minutes=30, booking_window_days=60
    )
    assert "30 minutes" in text
    assert "60 days" in text


def test_summary_single_active_day():
    days = [
        {"day_of_week": 2, "is_active": True, "open_time": time(10, 0), "close_time": time(12, 0)},
        {"day_of_week": 4, "is_active": True, "open_time": time(10, 0), "close_time": time(12, 0)},
    ]
    text = format_weekly_hours_summary(days, "UTC")
    # Non-consecutive identical days must NOT be grouped
    assert "Wednesday: 10:00 AM – 12:00 PM" in text
    assert "Friday: 10:00 AM – 12:00 PM" in text
    assert "Wednesday to Friday" not in text


def test_summary_all_closed_returns_empty():
    days = [
        {"day_of_week": i, "is_active": False, "open_time": None, "close_time": None}
        for i in range(7)
    ]
    assert format_weekly_hours_summary(days, "UTC") == ""


def test_summary_handles_missing_days():
    text = format_weekly_hours_summary(
        [{"day_of_week": 0, "is_active": True, "open_time": time(9, 0), "close_time": time(17, 0)}],
        "UTC",
    )
    assert "Monday: 9:00 AM – 5:00 PM" in text
    assert "Tuesday to Sunday: Closed" in text


# ── question gating ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message,expected",
    [
        ("what are your timings?", True),
        ("What are the timings for bookings", True),
        ("when do you open?", True),
        ("kya timing hai?", True),
        ("that timing works for me", False),
        ("this schedule is fine", False),
    ],
)
def test_looks_like_question(message, expected):
    assert _looks_like_question(message) == expected


def test_timing_keyword_matches_schedule_query():
    assert is_schedule_business_hours_query("what are your timings?")
    assert is_schedule_business_hours_query("booking timing kya hai?")


# ── load_weekly_hours_block ──────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeDB:
    """Returns queued results in order: schedule, days, exceptions."""

    def __init__(self, results):
        self._results = results
        self.calls = 0

    async def execute(self, _stmt):
        self.calls += 1
        return self._results[min(self.calls, len(self._results)) - 1]


def _schedule_row(tz="Asia/Kolkata", slot=30, window=60):
    return type(
        "Sched",
        (),
        {"timezone": tz, "slot_duration_minutes": slot, "booking_window_days": window},
    )()


def _day_row(idx, active, open_t=None, close_t=None):
    return type(
        "Day",
        (),
        {
            "day_of_week": idx,
            "is_active": active,
            "open_time": open_t,
            "close_time": close_t,
        },
    )()


@pytest.mark.asyncio
async def test_load_weekly_hours_block_builds_prompt_block():
    db = _FakeDB([_Rows([_schedule_row()]), _Rows([_day_row(i, i < 5, time(9, 0), time(17, 0)) for i in range(7)]), _Rows([])])
    block = await load_weekly_hours_block(db, "00000000-0000-0000-0000-000000000001")
    assert block.startswith("## BUSINESS HOURS")
    assert "Monday to Friday: 9:00 AM – 5:00 PM" in block
    assert "Asia/Kolkata" in block
    assert "30 minutes" in block
    assert "Only start the booking flow" in block


@pytest.mark.asyncio
async def test_load_weekly_hours_block_empty_without_schedule():
    db = _FakeDB([_Rows([])])
    assert await load_weekly_hours_block(db, "00000000-0000-0000-0000-000000000001") == ""


# ── lookup_schedule_business_hours_answer ────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_weekly_summary_for_generic_timing_question():
    db = _FakeDB(
        [
            _Rows([_schedule_row()]),
            _Rows([_day_row(i, i < 5, time(9, 0), time(17, 0)) for i in range(7)]),
            _Rows([]),
        ]
    )
    answer = await lookup_schedule_business_hours_answer(
        db=db,
        org_id="00000000-0000-0000-0000-000000000001",
        user_message="what are your timings?",
        effective_timezone="Asia/Kolkata",
        org_timezone="Asia/Kolkata",
    )
    assert answer is not None
    assert answer["status"] == "weekly_hours"
    assert "Monday to Friday" in answer["response_text"]


@pytest.mark.asyncio
async def test_lookup_ignores_non_question_statements():
    db = _FakeDB([_Rows([_schedule_row()]), _Rows([])])
    answer = await lookup_schedule_business_hours_answer(
        db=db,
        org_id="00000000-0000-0000-0000-000000000001",
        user_message="that timing works for me",
        effective_timezone="Asia/Kolkata",
        org_timezone="Asia/Kolkata",
    )
    assert answer is None


@pytest.mark.asyncio
async def test_lookup_skips_weekly_summary_mid_booking_flow():
    # Mid-flow questions must fall through to the LLM (which has the hours
    # block in its system prompt) so the booking flow is never interrupted.
    db = _FakeDB([_Rows([_schedule_row()]), _Rows([])])
    answer = await lookup_schedule_business_hours_answer(
        db=db,
        org_id="00000000-0000-0000-0000-000000000001",
        user_message="is that timing okay?",
        effective_timezone="Asia/Kolkata",
        org_timezone="Asia/Kolkata",
        in_booking_flow=True,
    )
    assert answer is None


@pytest.mark.asyncio
async def test_lookup_specific_day_path_unchanged():
    # Sunday (2026-01-04) closed in this schedule
    db = _FakeDB(
        [
            _Rows([_schedule_row()]),
            _Rows([_day_row(i, i < 5, time(9, 0), time(17, 0)) for i in range(7)]),
            _Rows([]),
        ]
    )
    answer = await lookup_schedule_business_hours_answer(
        db=db,
        org_id="00000000-0000-0000-0000-000000000001",
        user_message="are you open on sunday 2026-01-04?",
        effective_timezone="Asia/Kolkata",
        org_timezone="Asia/Kolkata",
    )
    assert answer is not None
    assert answer["status"] == "closed"
    assert "2026-01-04" in answer["response_text"]


# ── build_system_prompt injection ────────────────────────────────────────


def test_system_prompt_includes_hours_block():
    config = {
        "business_name": "Test Clinic",
        "persona": {"agent_name": "A", "tone": "friendly"},
        "timezone": "UTC",
    }
    prompt = build_system_prompt(
        config,
        [{"name": "name", "type": "text", "required": True}],
        hours_block="## BUSINESS HOURS (authoritative)\n- Monday: 9:00 AM – 5:00 PM",
    )
    assert "## BUSINESS HOURS" in prompt
    assert "Monday: 9:00 AM – 5:00 PM" in prompt


def test_system_prompt_without_hours_block_has_no_placeholder():
    config = {
        "business_name": "Test Clinic",
        "persona": {"agent_name": "A", "tone": "friendly"},
        "timezone": "UTC",
    }
    prompt = build_system_prompt(
        config, [{"name": "name", "type": "text", "required": True}]
    )
    assert "BUSINESS HOURS" not in prompt
