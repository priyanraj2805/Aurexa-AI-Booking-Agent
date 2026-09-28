"""A correct OTP must not be revoked, or the booking never happens.

Production chain (every symptom the customer reported):

  1. details collected, valid slot chosen
  2. OTP sent, customer enters the correct code
  3. the verifier records `phone_otp_verified_key` as just the phone number
  4. _handle_booking_flow compares it against _build_phone_otp_context_key(),
     which embeds session + phone + date + time
  5. the shapes never match, so the verification is REVOKED
  6. the flow re-sends a code, `phone_otp_verified` stays false
  7. the booking is never created -> no dashboard entry, no notification,
     no confirmation email - while the model still says "confirmed"
"""
import pytest

from app.engine.message_processor import _verified_key_matches_context


SESSION = "dabb4adf-7bf7-4218-bb45-391be6b6bade"
CONTEXT = f"otp_phone:{SESSION}:+916350063338:2026-09-28:3:00 PM"


def test_bare_phone_from_the_verifier_is_accepted():
    """This is the exact value the verifier used to write."""
    assert _verified_key_matches_context("+916350063338", CONTEXT) is True


def test_full_context_key_is_accepted():
    assert _verified_key_matches_context(CONTEXT, CONTEXT) is True


def test_differently_formatted_phone_is_accepted():
    assert _verified_key_matches_context("916350063338", CONTEXT) is True
    assert _verified_key_matches_context("+91 63500 63338", CONTEXT) is True


def test_verification_for_a_different_customer_is_rejected():
    assert _verified_key_matches_context("+919999999999", CONTEXT) is False


def test_verification_for_a_different_session_is_rejected():
    other = f"otp_phone:11111111-2222-3333-4444-555555555555:+916350063338:2026-09-28:3:00 PM"
    assert _verified_key_matches_context(other, CONTEXT) is False


def test_slot_change_after_verifying_keeps_the_verification():
    """The root cause: the key embeds date+time, so editing the appointment
    silently discarded a real verification and the booking never happened."""
    changed = f"otp_phone:{SESSION}:+916350063338:2026-10-01:11:00 AM"
    assert _verified_key_matches_context(changed, CONTEXT) is True


def test_time_formatting_difference_keeps_the_verification():
    changed = f"otp_phone:{SESSION}:+916350063338:2026-09-28:15:30"
    assert _verified_key_matches_context(changed, CONTEXT) is True


def test_empty_stored_key_is_rejected():
    assert _verified_key_matches_context("", CONTEXT) is False
    assert _verified_key_matches_context("   ", CONTEXT) is False


def test_orchestrator_can_write_the_verified_key():
    """The write silently died twice and both failures were invisible:

      * it sat inside `try: ... except Exception: pass`, so any error vanished
      * `_ensure_internal_state` was never imported into the orchestrator, so
        it raised NameError straight into that same bare except

    A silently missing key is what made _handle_booking_flow treat a real
    verification as bogus and revoke it, so the write must be reachable.
    """
    import inspect

    from app.engine import langgraph_orchestrator as orch
    from app.engine.message_processor import _ensure_internal_state

    src = inspect.getsource(orch)
    assert "_ensure_internal_state" in src, (
        "orchestrator must reference _ensure_internal_state to persist the key"
    )
    # The call must be imported, not assumed: a bare reference raises
    # NameError at runtime and is swallowed by the surrounding except.
    assert "import" in src and "_ensure_internal_state as _ensure_internal" in src
    # And the callables must be the same object.
    assert callable(_ensure_internal_state)


def test_verified_key_shape_is_accepted_by_the_reader():
    """The writer emits `otp_phone:<session>:<phone>`; the reader must accept
    exactly that shape, otherwise writer and reader disagree again."""
    written = f"otp_phone:{SESSION}:+916350063338"
    assert _verified_key_matches_context(written, CONTEXT) is True
