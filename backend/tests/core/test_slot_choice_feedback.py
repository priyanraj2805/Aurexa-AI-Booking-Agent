"""Rejected date/time wording and admin closing days.

Both features are presentation only: they never change which date or time is
accepted, only how clearly the customer is told about it.
"""
import pytest

from app.engine.langgraph_orchestrator import _unmatched_time_choice
from app.engine.message_processor import format_closed_days_note

OFFERED = ["9:00 AM", "12:00 PM", "1:30 PM", "3:00 PM"]


# ── rejected time ──────────────────────────────────────────────────────

def test_unoffered_time_is_reported_as_rejected() -> None:
    assert _unmatched_time_choice("10:30 AM", OFFERED) == "10:30 AM"
    assert _unmatched_time_choice("7:15 pm", OFFERED) == "7:15 pm"


@pytest.mark.parametrize("picked", OFFERED)
def test_offered_time_is_never_flagged(picked: str) -> None:
    assert _unmatched_time_choice(picked, OFFERED) == ""


def test_time_formatting_variations_still_match_an_offered_slot() -> None:
    """The customer may type 12:00, 12pm or 12:00 PM. All are valid, so none
    may be rejected as invalid."""
    assert _unmatched_time_choice("12:00", OFFERED) == ""
    assert _unmatched_time_choice("12pm", OFFERED) == ""
    assert _unmatched_time_choice("12:00 pm", OFFERED) == ""
    assert _unmatched_time_choice("1:30 PM", OFFERED) == ""


@pytest.mark.parametrize(
    "message",
    [
        "thanks",
        "Okayy",
        "3 of us",
        "hi",
        "yes",
        "cancel",
        "what are your timings?",
        "",
    ],
)
def test_non_time_messages_are_never_flagged(message: str) -> None:
    """Only a genuine clock time may be called invalid. Anything else must be
    left for the normal flow to interpret."""
    assert _unmatched_time_choice(message, OFFERED) == ""


def test_nothing_is_flagged_when_no_slots_were_offered() -> None:
    assert _unmatched_time_choice("10:30 AM", []) == ""
    assert _unmatched_time_choice("10:30 AM", None) == ""


# ── closing days from the admin schedule ───────────────────────────────

def test_no_closing_days_claims_nothing() -> None:
    """When the business is open every day we must not invent a closed day."""
    assert format_closed_days_note([]) == ""
    assert format_closed_days_note(None) == ""


def test_single_closing_day() -> None:
    assert format_closed_days_note([6]) == "We are closed on Sunday."
    assert format_closed_days_note([5]) == "We are closed on Saturday."


def test_two_closing_days_read_naturally() -> None:
    assert format_closed_days_note([5, 6]) == "We are closed on Saturday and Sunday."


def test_many_closing_days_use_a_list() -> None:
    out = format_closed_days_note([0, 1, 5, 6])
    assert out == "We are closed on Monday, Tuesday, Saturday and Sunday."


def test_closing_days_are_deduplicated_and_ordered() -> None:
    assert format_closed_days_note([6, 5, 6]) == "We are closed on Saturday and Sunday."


def test_out_of_range_weekdays_are_ignored() -> None:
    assert format_closed_days_note([99]) == ""


# ── edit then confirm ──────────────────────────────────────────────────

def test_edit_summary_returns_after_a_change() -> None:
    """Regression from chat 0d15f17d.

    The customer edited the time and got "Your appointment time is now
    updated... anything else?" with no summary and no way to confirm. The
    booking only completed by luck on a later turn.

    The EDIT_DATA re-summary lived solely in the non-PROCESS_BOOKING branch,
    so when the LLM read a complete edited booking as PROCESS_BOOKING the
    summary was skipped entirely.
    """
    from app.engine.message_processor import _build_edit_summary

    def _fd(icon, label, ftype):
        return type(
            "F", (),
            {"icon": icon, "label": label, "type": ftype, "unit": None, "options": None},
        )()

    # fd_map drives which fields the summary renders, so the test must supply
    # the same field definitions the real flow passes in.
    fd_map = {
        "name": _fd("\U0001f468", "Full Name", "text"),
        "email": _fd("\U0001f4e7", "Email Address", "email"),
        "phone": _fd("\U0001f4de", "Phone Number", "phone"),
        "date": _fd("\U0001f4c5", "Preferred Date", "date"),
        "time": _fd("\u23f0", "Preferred Time", "time"),
    }

    class _Session:
        collected_data = {
            "name": "alexa",
            "email": "alexa@yopmail.com",
            "phone": "+916350063338",
            "date": "2026-09-30",
            "time": "3:00 PM",
        }
        session_status = "ACTIVE"

    msg, directive = _build_edit_summary(_Session(), {"time": "3:00 PM"}, fd_map)

    assert directive == "REQUEST_CONFIRMATION", (
        "after an edit the customer must land back on the confirmation step, "
        "never straight into booking or a generic 'anything else'"
    )
    # The revised value must be visible, and the edit marked.
    assert "3:00 PM" in msg
    assert "\u270f\ufe0f" in msg  # edited-field marker


def test_edit_does_not_reconfirm_an_already_booked_session() -> None:
    """A reschedule of a real booking is a different flow and must not be
    downgraded into a pre-confirmation summary."""
    from app.engine.message_processor import _is_unverified_booking_claim

    class _Active:
        session_status = "ACTIVE"

    class _Confirmed:
        session_status = "CONFIRMED"

    class _Confirming:
        session_status = "CONFIRMING"

    assert _is_unverified_booking_claim(_Active()) is False
    assert _is_unverified_booking_claim(_Confirmed()) is True
    assert _is_unverified_booking_claim(_Confirming()) is True
