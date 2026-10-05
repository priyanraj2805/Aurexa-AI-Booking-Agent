"""Explicit slot-availability questions must render the computed slot list.

Production failure: "Hello bro tell me timings for appointment yrr" and
"Tell me available times bro" (with a date already collected) got the
graph's empty-handed answer — "I don't have a list of open time slots" —
while the chatter guard skipped rendering. The graph has no inventory in
its prompt, so only the renderer can answer these.
"""
import pytest

from app.engine.langgraph_orchestrator import _asks_for_available_slots


@pytest.mark.parametrize(
    "text",
    [
        "Hello bro tell me timings for appointment yrr",
        "Tell me available times bro",
        "Show me the available slots for tomorrow",
        "Which slots are free on Friday?",
        "What times are open today?",
        "Give me the timings please",
        "TIMINGS BATAO",
    ],
)
def test_explicit_slot_requests_detected(text: str) -> None:
    assert _asks_for_available_slots(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "do you have parking?",
        "what time do you close?",
        "which dates are open?",
        "3pm",
        "My name is Priyank",
        "thanks!",
        "",
        "I want to book an appointment",
    ],
)
def test_chatter_and_other_paths_not_detected(text: str) -> None:
    assert _asks_for_available_slots(text) is False


def test_date_capture_delta_counts_as_provided() -> None:
    """The merge diff is authoritative: relative dates ("tomorrow") and
    dates inside sentences never survive a strict full-message parse, so
    without the delta signal their slots were never auto-rendered."""
    from app.engine.langgraph_orchestrator import _date_provided_this_turn

    assert (
        _date_provided_this_turn("tomorrow", "2026-11-06", {"date": "2026-11-06"})
        is True
    )
    assert (
        _date_provided_this_turn(
            "book for November 5th please",
            "2026-11-05",
            {"date": "2026-11-05"},
        )
        is True
    )


def test_restated_matching_date_counts_as_provided() -> None:
    from app.engine.langgraph_orchestrator import _date_provided_this_turn

    assert _date_provided_this_turn("2026-11-05", "2026-11-05", {}) is True
    assert _date_provided_this_turn("2026-11-06", "2026-11-05", {}) is False
    assert _date_provided_this_turn("tell me timings", "2026-11-05", {}) is False
    assert _date_provided_this_turn("", "2026-11-05", {}) is False
