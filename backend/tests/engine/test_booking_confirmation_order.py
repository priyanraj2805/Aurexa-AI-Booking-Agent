"""Required booking order: details -> summary -> confirm -> OTP -> code -> booked.

The bug: the OTP was sent as soon as the fields were complete, so the customer
received a code first and the summary afterwards. Clicking Confirm then
re-displayed the summary instead of moving on to verification, and the booking
was never created (no dashboard entry, no notification, no confirmation mail).
"""
import pytest

from app.engine.langgraph_orchestrator import should_run_booking_gate


BASE = dict(
    require_otp=False,
    require_phone_otp=True,   # admin chose phone OTP
    otp_verified=False,
    phone_otp_verified=False,
)


# ── step 1: details done, NOT confirmed -> summary, NO otp ──────────────

def test_no_otp_before_the_customer_confirms():
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=[], user_confirmed=False, **BASE
    ) is False


def test_no_otp_before_confirm_even_on_a_confirmation_directive():
    assert should_run_booking_gate(
        directive="REQUEST_CONFIRMATION", missing_fields=[],
        user_confirmed=False, **BASE
    ) is False


# ── step 2: confirmed, OTP not verified -> send the code ───────────────

@pytest.mark.parametrize(
    "text_ok", ["Okayy", "confirm", "yes", "book it"]
)
def test_otp_is_sent_after_confirmation(text_ok):
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=[],
        user_confirmed=True, **BASE
    ) is True


# ── step 3: code accepted -> booking proceeds ──────────────────────────

def test_gate_still_runs_once_verified():
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=[],
        user_confirmed=True, require_otp=False, require_phone_otp=True,
        otp_verified=False, phone_otp_verified=True,
    ) is True


# ── incomplete details still ask for the next field ────────────────────

def test_missing_fields_block_the_summary_and_the_otp():
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=["time"],
        user_confirmed=False, **BASE
    ) is True  # the booking flow asks for the field; it does not send a code


# ── legacy callers that report no confirmation keep old behaviour ──────

def test_none_confirmation_preserves_previous_behaviour():
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=[],
        user_confirmed=None, **BASE
    ) is True


def test_admin_without_otp_is_unaffected():
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=[],
        user_confirmed=False, require_otp=False, require_phone_otp=False,
        otp_verified=False, phone_otp_verified=False,
    ) is False


# ── interactive button taps are confirmations ──────────────────────────
# Tapping "Confirm" on WhatsApp sends the button ID, not prose. A
# natural-language-only check rejected "btn_confirm", so clicking Confirm
# re-displayed the summary forever and no OTP was ever sent.

@pytest.mark.parametrize(
    "tap",
    [
        "btn_confirm",
        "btn_confirm_booking",
        "confirm_booking",
        "Confirm Booking",
        "\u2705 Confirm",
        "CONFIRM",
    ],
)
def test_confirm_button_tap_counts_as_confirmation(tap):
    from app.engine.langgraph_orchestrator import _is_user_confirmation

    assert _is_user_confirmation(tap) is True


@pytest.mark.parametrize("tap", ["btn_edit", "btn_cancel", "Edit Details", "\u270f\ufe0f Change"])
def test_edit_and_cancel_taps_are_not_confirmations(tap):
    from app.engine.langgraph_orchestrator import _is_user_confirmation

    assert _is_user_confirmation(tap) is False


def test_button_confirm_tap_unblocks_the_otp_gate():
    """A confirm tap must move the flow on to verification, not re-show the
    summary."""
    from app.engine.langgraph_orchestrator import _is_user_confirmation

    confirmed = _is_user_confirmation("btn_confirm")
    assert should_run_booking_gate(
        directive="PROCESS_BOOKING", missing_fields=[],
        user_confirmed=confirmed, **BASE
    ) is True


# ── the booking turn: gate MUST run so _execute_booking is reached ──────

def test_gate_runs_on_the_code_turn_once_the_otp_is_accepted():
    """Regression: the gate used to bail out with `if not otp_pending`.

    That short-circuit switched the gate OFF on exactly the turn the booking
    had to be written, so `_execute_booking` never ran. The customer got the
    model's "your appointment is confirmed" text with no Booking row, no
    calendar event, no notification and no email behind it.
    """
    assert should_run_booking_gate(
        directive="TEXT_INPUT",          # the model said nothing useful
        missing_fields=[],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=True,        # the code was just accepted
        user_confirmed=False,            # a bare 6-digit entry is not a word
    ) is True


def test_gate_stays_off_on_the_code_turn_if_fields_are_missing():
    assert should_run_booking_gate(
        directive="TEXT_INPUT",
        missing_fields=["time"],
        require_otp=False,
        require_phone_otp=True,
        otp_verified=False,
        phone_otp_verified=True,
        user_confirmed=False,
    ) is False
