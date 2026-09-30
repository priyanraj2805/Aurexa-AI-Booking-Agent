"""Regression: a verification code must never be sent before confirmation.

Required order: details -> summary -> CONFIRM -> OTP -> code -> booking.

Two independent defects both violated it, and both are pinned here.

Defect 1 (standalone OTP path). ``should_run_booking_gate`` correctly refuses
to run when the customer has not confirmed, so the booking flow shows the
review summary. But a SECOND, parallel OTP-send block keyed only off
``otp_required`` ran anyway, so the customer received a code for a booking
they had never seen the summary of and had not agreed to.

Defect 2 (carried-over intent). ``agent/graph.py`` does not classify intent --
it returns ``state["current_intent"]``, i.e. the value the orchestrator fed in
from the previous turn. A session that ended on CONFIRM_BOOKING therefore
armed PROCESS_BOOKING for whatever the customer typed next.
"""

import pytest

from app.engine.langgraph_orchestrator import (
    _is_user_confirmation,
    may_send_otp_standalone,
    should_run_booking_gate,
)

_CONFIRMS = ["yes", "Yes", "confirm", "btn_confirm", "ok", "sure", "yess", "okayy"]
_NON_CONFIRMS = [
    "what are your opening hours?",
    "hello",
    "3:00 PM",
    "my email is a@b.com",
    "I want to book a session",
    "cancel my booking",
    "123456",
]


# ── invariant 1: details without confirmation -> no OTP ─────────────────────
def test_gate_refuses_to_run_before_confirmation() -> None:
    gate = should_run_booking_gate(
        directive="PROCESS_BOOKING",
        missing_fields=[],
        require_otp=True,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=False,
        user_confirmed=False,
    )
    assert gate is False, "gate must not run the booking flow before confirmation"


def test_standalone_otp_block_is_suppressed_before_confirmation() -> None:
    """The bug: the gate said no, and the second OTP path sent the code anyway."""
    assert (
        may_send_otp_standalone(
            booking_gate_handled=False,
            otp_required=True,
            user_confirmed=False,
        )
        is False
    )


def test_standalone_otp_block_never_sends_on_the_otp_turn() -> None:
    """On the code turn the gate handles everything; the fallback must stay off."""
    assert (
        may_send_otp_standalone(
            booking_gate_handled=True,
            otp_required=True,
            user_confirmed=False,
        )
        is False
    )


def test_standalone_otp_block_needs_a_real_requirement() -> None:
    assert (
        may_send_otp_standalone(
            booking_gate_handled=False,
            otp_required=False,
            user_confirmed=True,
        )
        is False
    )


# ── invariant 2: explicit confirmation authorises the OTP stage ─────────────
def test_gate_runs_once_the_customer_confirms() -> None:
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=False,
            phone_otp_verified=False,
            user_confirmed=True,
        )
        is True
    )


def test_standalone_otp_block_allowed_after_confirmation() -> None:
    assert (
        may_send_otp_standalone(
            booking_gate_handled=False,
            otp_required=True,
            user_confirmed=True,
        )
        is True
    )


def test_unknown_confirmation_state_keeps_legacy_behaviour() -> None:
    """Callers that cannot report a confirmation (None) are unaffected."""
    assert (
        may_send_otp_standalone(
            booking_gate_handled=False,
            otp_required=True,
            user_confirmed=None,
        )
        is True
    )
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=False,
            phone_otp_verified=False,
            user_confirmed=None,
        )
        is True
    )


# ── invariant 3: a code alone must not create a booking ────────────────────
def test_a_bare_code_is_not_treated_as_a_confirmation() -> None:
    """A 6-digit code is verified by the OTP branch. It must never be read as
    the customer agreeing to the booking, or the code would authorise itself."""
    assert _is_user_confirmation("123456") is False
    assert (
        may_send_otp_standalone(
            booking_gate_handled=False,
            otp_required=True,
            user_confirmed=_is_user_confirmation("123456"),
        )
        is False
    )


def test_gate_still_refuses_when_only_a_code_was_entered() -> None:
    """Code entered, nothing verified yet, nothing confirmed."""
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=False,
            phone_otp_verified=False,
            user_confirmed=False,
        )
        is False
    )


# ── invariant 4: verified OTP after a real confirmation may book ───────────
def test_gate_runs_on_the_verified_code_turn() -> None:
    """The code is only ever sent after a confirmation, so once it is accepted
    the gate must run and write the booking -- even though this turn's text is
    the code itself and therefore not a fresh confirmation."""
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=True,
            phone_otp_verified=False,
            user_confirmed=False,
        )
        is True
    )


# ── invariant 5: the non-OTP flow is untouched ─────────────────────────────
def test_admin_with_no_otp_configured_is_unaffected() -> None:
    assert (
        should_run_booking_gate(
            directive="PROCESS_BOOKING",
            missing_fields=[],
            require_otp=False,
            require_phone_otp=False,
            otp_verified=False,
            phone_otp_verified=False,
            user_confirmed=True,
        )
        is False
    )
    assert (
        may_send_otp_standalone(
            booking_gate_handled=False,
            otp_required=False,
            user_confirmed=True,
        )
        is False
    )


def test_missing_fields_still_block_the_gate() -> None:
    assert (
        should_run_booking_gate(
            directive="REQUEST_CONFIRMATION",
            missing_fields=["email"],
            require_otp=True,
            require_phone_otp=False,
            otp_verified=False,
            phone_otp_verified=False,
            user_confirmed=True,
        )
        is False
    )


@pytest.mark.parametrize("text", _CONFIRMS)
def test_real_confirmations_are_recognised(text: str) -> None:
    assert _is_user_confirmation(text) is True


@pytest.mark.parametrize("text", _NON_CONFIRMS)
def test_unrelated_messages_are_never_confirmations(text: str) -> None:
    assert _is_user_confirmation(text) is False
