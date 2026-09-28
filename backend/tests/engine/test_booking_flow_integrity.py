"""Booking-flow integrity: mandatory OTP and truthful confirmation.

Two production failures are pinned here.

1. The mandatory-OTP gate only ran for the PROCESS_BOOKING directive. When
   the model answered REQUEST_CONFIRMATION instead, no verification code was
   ever sent, and the customer went straight from "here are your details" to
   "your appointment is confirmed" — with no Booking row written, so nothing
   appeared in the admin dashboard or notifications.

2. Replies also carried confirmation PROSE inside ordinary directives, which
   the directive-level guard could not catch.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.core.wa_text import (
    format_outbound,
    normalize_bold,
    normalize_bullets,
    normalize_spacing,
    strip_leaked_headers,
)
from app.engine.langgraph_orchestrator import should_run_booking_gate


# ── 1. the OTP gate must not depend on the model's chosen directive ────

def test_gate_runs_for_request_confirmation_when_otp_pending():
    """The exact bypass: all fields collected, phone OTP on, not verified."""
    assert should_run_booking_gate(
        directive="REQUEST_CONFIRMATION",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=False,
    ) is True


def test_gate_still_runs_after_the_otp_is_verified():
    """Regression: the gate used to switch ITSELF OFF once a code was accepted.

    The derived "otp still pending" flags are False immediately after
    verification, and the gate was gated on them. So on the one turn the
    booking was supposed to be created, the booking flow never ran, nothing was
    persisted, and the customer was told "verification successful, your booking
    is confirmed".
    """
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING",
        missing_fields=[],
        require_otp=False,      # admin config: email OTP off
        require_phone_otp=True,  # admin config: phone OTP on
        otp_verified=False,
        phone_otp_verified=True,  # code was just accepted
    ) is True


def test_gate_runs_for_confirmation_directive_after_verification():
    assert should_run_booking_gate(
        directive="REQUEST_CONFIRMATION",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=True,
    ) is True, (
        "with verification satisfied the gate MUST run: this is the turn the "
        "booking row, calendar event, notification and email get written. "
        "It used to return False here, which left only the model's "
        "'your appointment is confirmed' text with nothing behind it."
    )


def test_gate_is_off_when_admin_disabled_all_otp():
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=False,
        otp_verified=False,
        phone_otp_verified=False,
    ) is False


@pytest.mark.parametrize(
    "directive",
    ["PROCESS_BOOKING", "REQUEST_CONFIRMATION", "BOOKING_SUMMARY_CARD", "TEXT_INPUT"],
)
def test_gate_runs_for_every_terminal_looking_directive(directive):
    assert should_run_booking_gate(
        directive=directive,
        missing_fields=[],
        require_otp=True,
        require_phone_otp=False,
        otp_verified=False,
        phone_otp_verified=False,
    ) is True


def test_gate_runs_while_fields_are_still_missing():
    """Fields outstanding means the booking handler asks for them, not OTP."""
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING",
        missing_fields=["time"],
        require_otp=True,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=False,
    ) is True


def test_gate_skipped_once_otp_is_verified():
    """Renamed intent: the gate is NOT skipped once OTP is verified.

    Every required code has been accepted, so the gate must run and hand off
    to _handle_booking_flow -> _execute_booking. Skipping it here is what
    left bookings unwritten while the customer was told they were confirmed.
    """
    assert should_run_booking_gate(
        directive="REQUEST_CONFIRMATION",
        missing_fields=[],
        require_otp=True,
        require_phone_otp=True,
        otp_verified=True,
        phone_otp_verified=True,
    ) is True


def test_gate_skipped_when_admin_disabled_otp():
    assert should_run_booking_gate(
        directive="REQUEST_CONFIRMATION",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=False,
        otp_verified=False,
        phone_otp_verified=False,
    ) is False


def test_gate_waits_for_missing_fields_on_a_confirmation_directive():
    """A confirmation directive with fields outstanding must not claim done."""
    assert should_run_booking_gate(
        directive="REQUEST_CONFIRMATION",
        missing_fields=["email"],
        require_otp=True,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=False,
    ) is False


def test_unrelated_directive_is_not_forced_into_the_gate():
    """Knowledge/other turns must not be hijacked into an OTP send."""
    assert should_run_booking_gate(
        directive="ANSWER_KNOWLEDGE",
        missing_fields=[],
        require_otp=True,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=False,
    ) is False


# ── a question is not a field value ────────────────────────────────────

def test_knowledge_question_is_never_stored_as_the_customer_name():
    """The exact production bug: the question became `name`.

    A model handed "I want to know about dotstark" during the full-name step
    returned the whole sentence as the name. The merge accepted it, dropped
    `name` from the missing queue, and the customer was never asked again —
    so a booking could carry "I want to know about dotstark" as the name.
    """
    from app.engine.state_merge import apply_state_merge

    out = apply_state_merge(
        current_fields={},
        extracted_fields={"name": "I want to know about dotstark"},
        required_fields=["name", "email", "phone", "date", "time"],
    )
    assert out["collected_fields"].get("name") is None, "question stored as name"
    assert "name" in out["missing_fields"]
    assert "name" in out["follow_up_fields"]
    assert "name" in out["diagnostics"]["invalid_fields"]


@pytest.mark.parametrize(
    "value",
    [
        "I want to know about dotstark",
        "What services do you provide?",
        "do you offer hair cuts and beard trims please",
        "one two three four five six seven",
    ],
)
def test_sentence_shaped_names_are_rejected(value):
    from app.engine.state_merge import reject_implausible_values

    rejected = reject_implausible_values({"name": value}, {"name", "email"})
    assert "name" in rejected


@pytest.mark.parametrize(
    "value",
    ["William", "William Kelvin", "Dr Ananya Sharma", "Priyanshu Raj",
     "Jean-Luc Picard", "Mary Jane Watson-Kelly"],
)
def test_real_names_are_accepted(value):
    from app.engine.state_merge import reject_implausible_values

    assert reject_implausible_values({"name": value}, {"name", "email"}) == {}


def test_non_name_fields_are_not_affected():
    """Only identity free-text is checked here; other fields have own validators."""
    from app.engine.state_merge import reject_implausible_values

    long_note = "a" * 200
    assert reject_implausible_values(
        {"email": "a@b.com", "date": long_note, "time": long_note}, {"email", "date", "time"}
    ) == {}


# ── the booking itself is authorised by state, not by the intent label ──

def test_booking_created_when_state_is_satisfied_even_with_odd_intent():
    """The production bypass: model returned a non-CONFIRM intent label."""
    from app.engine.message_processor import should_create_booking

    assert should_create_booking(
        intent="PROVIDE_DATA",          # <- what the model actually returned
        user_confirmed=True,            # customer said "okay"
        session_status="ACTIVE",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=True,
    ) is True


@pytest.mark.parametrize(
    "kwargs,expected_false",
    [
        # Fields still outstanding.
        (dict(user_confirmed=True, missing_fields=["time"]), True),
        # Phone OTP required but not verified -> fail closed.
        (dict(user_confirmed=True, require_phone_otp=True, phone_otp_verified=False), True),
        # Email OTP required but not verified -> fail closed.
        (dict(user_confirmed=True, require_otp=True, otp_verified=False), True),
        # Session no longer active -> fail closed.
        (dict(user_confirmed=True, session_status="CONFIRMED"), True),
        (dict(user_confirmed=True, session_status="CANCELLED"), True),
    ],
)
def test_booking_blocked_when_any_obligation_is_unmet(kwargs, expected_false):
    from app.engine.message_processor import should_create_booking

    base = dict(
        intent="CONFIRM_BOOKING",
        user_confirmed=True,
        session_status="ACTIVE",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=True,
    )
    base.update(kwargs)
    result = should_create_booking(**base)
    assert result is (not expected_false)


def test_accepted_code_counts_as_the_confirmation():
    """A bare 6-digit entry is the confirmation, in effect.

    The code is only ever sent AFTER the customer confirmed the summary, so on
    the code turn the booking must proceed rather than re-showing the summary.
    """
    from app.engine.message_processor import should_create_booking

    assert should_create_booking(
        intent="CONFIRM_BOOKING",
        user_confirmed=False,          # a 6-digit code is not a confirmation word
        session_status="ACTIVE",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=True,      # the code was accepted
    ) is True


def test_no_confirmation_and_no_code_blocks_the_booking():
    from app.engine.message_processor import should_create_booking

    assert should_create_booking(
        intent="CONFIRM_BOOKING",
        user_confirmed=False,
        session_status="ACTIVE",
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=False,
    ) is False


def test_mid_otp_off_topic_question_says_we_are_a_booking_assistant():
    """Mid-verification, an unrelated question used to be swallowed entirely.

    The customer got a bare "Please enter the verification code sent to X."
    with no acknowledgement of what they had actually asked, which reads as
    being stonewalled. The reply must now state the limit and re-prompt.
    """
    import inspect

    from app.engine import langgraph_orchestrator as orch

    src = inspect.getsource(orch)
    assert "booking assistant" in src, (
        "the pending-OTP reply must tell the user we only help with bookings"
    )
    assert "I don't have information about that" in src


def test_terminal_session_statuses_are_defined():
    """A finished booking must stay finished.

    The graph keeps running after _handle_booking_flow, and its in-memory copy
    of the session still said ACTIVE, so the final save wrote ACTIVE back over
    the CONFIRMED that _execute_booking had persisted. The customer then saw
    an unfinished session and was pushed into booking a second time.
    """
    from app.engine.langgraph_orchestrator import _TERMINAL_SESSION_STATUSES

    assert "CONFIRMED" in _TERMINAL_SESSION_STATUSES
    assert "CANCELLED" in _TERMINAL_SESSION_STATUSES
    assert "EXPIRED" in _TERMINAL_SESSION_STATUSES
    assert "ACTIVE" not in _TERMINAL_SESSION_STATUSES
    assert "CONFIRMING" not in _TERMINAL_SESSION_STATUSES


def test_booking_gate_falls_back_to_legacy_intent_check():
    """Callers that pass no confirmation signal behave exactly as before."""
    from app.engine.message_processor import should_create_booking

    assert should_create_booking(
        intent="CONFIRM_BOOKING", user_confirmed=None, session_status="ACTIVE",
        missing_fields=[], require_otp=False, require_phone_otp=True,
        otp_verified=False, phone_otp_verified=False,
    ) is True
    assert should_create_booking(
        intent="PROVIDE_DATA", user_confirmed=None, session_status="ACTIVE",
        missing_fields=[], require_otp=False, require_phone_otp=True,
        otp_verified=False, phone_otp_verified=False,
    ) is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("okay", True),
        ("Okayy", True),        # real typo customers actually send
        ("okk", True),
        ("yess", True),
        ("comfirm", True),
        ("confirm", True),
        ("confirm booking", True),
        ("book my appointment", True),
        ("yes", True),
        ("book it", True),
        ("go ahead", True),
        ("123456", False),          # OTP entry, not a confirmation
        ("12 pm", False),
        ("29 September", False),
        ("what are your timings?", False),
        ("okay?", False),           # a question is never a confirmation
        # The original defect must stay fixed: sentences containing "k".
        ("I want to know about dotstark", False),
        ("book an appointment for me", False),
        ("kindly share the timings", False),
        # Negations and destructive asks must never authorise a booking.
        ("cancel booking", False),
        ("delete my appointment", False),
        ("do not book", False),
        ("", False),
    ],
)
def test_user_confirmation_signal(text, expected):
    """The booking authorisation signal comes from real words only."""
    from app.engine.langgraph_orchestrator import _is_user_confirmation

    assert _is_user_confirmation(text) is expected


# ── 2. a confirmation claim requires a persisted booking ───────────────

@pytest.mark.asyncio
async def test_booking_existence_check_is_false_without_rows(db_session):
    from app.engine.langgraph_orchestrator import _session_has_persisted_booking

    assert await _session_has_persisted_booking(db_session, str(uuid.uuid4())) is False


@pytest.mark.asyncio
async def test_booking_existence_check_is_true_with_a_row(db_session):
    from datetime import datetime, timezone

    from app.engine.langgraph_orchestrator import _session_has_persisted_booking
    from app.models.booking import Booking

    session_id = uuid.uuid4()
    db_session.add(
        Booking(
            id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            session_id=session_id,
            slot_id=uuid.uuid4(),
            customer_name="William Kelvin",
            customer_email="wk@example.com",
            org_slot_start_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    assert await _session_has_persisted_booking(db_session, str(session_id)) is True


def test_confirmation_claim_detector_catches_the_prose():
    from app.engine.user_visible_text import looks_like_confirmation_claim

    assert looks_like_confirmation_claim(
        "Your appointment is confirmed for September 29, 2026 at 12:00 PM."
    ) is True
    # The exact sentence the customer received with no booking stored.
    assert looks_like_confirmation_claim(
        "Thanks! Your appointment is set for September 29 at 12:00 PM."
    ) is True
    assert looks_like_confirmation_claim(
        "We have booked your appointment for tomorrow."
    ) is True
    # Questions, negations and mid-flow phrasing must NOT be claims.
    assert looks_like_confirmation_claim("Is your appointment confirmed?") is False
    assert looks_like_confirmation_claim("Your slot is not booked yet.") is False
    assert looks_like_confirmation_claim("The date is set to 2026-09-29.") is False
    assert looks_like_confirmation_claim("What date would you like?") is False


# ── 3. the redesigned reply format ─────────────────────────────────────

def test_leaked_prompt_header_is_removed():
    assert (
        strip_leaked_headers("BUSINESS HOURS\nThanks! What date would you like to come in?")
        == "Thanks! What date would you like to come in?"
    )


def test_normal_sentence_starting_with_capitals_is_kept():
    text = "Thanks William! Could you please share your email address?"
    assert strip_leaked_headers(text) == text


def test_double_asterisk_bold_becomes_whatsapp_bold():
    out = normalize_bold("**Name:** William")
    assert out == "*Name:* William"
    assert "**" not in out


def test_bold_is_idempotent():
    once = normalize_bold("**Date:** 2026-09-29")
    assert normalize_bold(once) == once


def test_literal_asterisk_is_not_treated_as_bold():
    out = normalize_bold("Rate us 5* anytime")
    assert out == "Rate us 5* anytime"


def test_bullets_are_normalised():
    out = normalize_bullets("Slots:\n- 9:00 AM\n* 10:30 AM\n+ 12:00 PM")
    assert out == "Slots:\n• 9:00 AM\n• 10:30 AM\n• 12:00 PM"


def test_spacing_gives_one_blank_line_between_blocks():
    out = normalize_spacing("Title\n\n\n\nBody   \n\n\n\nNext")
    assert out == "Title\n\nBody\n\nNext"


def test_confirmation_summary_has_bold_labels_and_spacing():
    from app.engine.message_processor import _build_confirmation_summary
    from app.schemas.agent import parse_field_definitions

    session = SimpleNamespace(
        collected_data={
            "name": "William Kelvin",
            "email": "wk@example.com",
            "phone": "+91 6350063338",
            "date": "2026-09-29",
            "time": "12:00 PM",
        }
    )
    fd_map = {fd.name: fd for fd in parse_field_definitions(
        ["name", "email", "phone", "date", "time"]
    )}
    text, directive = _build_confirmation_summary(session, fd_map)

    assert directive == "REQUEST_CONFIRMATION"
    assert "**" not in text, "double asterisks render literally on WhatsApp"
    assert "*" in text, "labels must be bolded"
    assert "William Kelvin" in text
    assert "2026-09-29" in text
    assert "12:00 PM" in text
    # Blank line between the title and the detail block.
    assert "\n\n" in text


def test_format_outbound_end_to_end_is_whatsapp_ready():
    raw = "**Booking**\n\n- Name: William\n- Date: 2026-09-29"
    out = format_outbound(raw)
    assert "**" not in out
    assert "• Name: William" in out
