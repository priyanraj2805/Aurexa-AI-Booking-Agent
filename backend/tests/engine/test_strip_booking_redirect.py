"""A question between booking steps is answered, not redirected.

Production symptom: a customer asked what the capital of Japan is and got
"The capital of Japan is Tokyo. Back to your booking, could you please share
a preferred date?" The answer arrived and was immediately buried under a push,
so it read as the bot refusing to engage.
"""
import pytest

from app.engine.langgraph_orchestrator import _strip_booking_redirect


CASES = [
    (
        "The capital of Japan is Tokyo. Back to your booking, could you please "
        "share a preferred date?",
        "The capital of Japan is Tokyo.",
    ),
    (
        "We offer Kentico development, Azure and Power Platform services. "
        "For your booking, could you please share your phone number?",
        "We offer Kentico development, Azure and Power Platform services.",
    ),
    (
        "The capital of Japan is Tokyo. Would you like to book an appointment "
        "instead?",
        "The capital of Japan is Tokyo.",
    ),
    (
        "You can reach us on +91 63500 63338 during working hours. Which date "
        "works for you?",
        "You can reach us on +91 63500 63338 during working hours.",
    ),
    (
        "We do offer machine learning services including patient risk "
        "classification. Let's get your appointment set up instead. Which "
        "date works for you?",
        "We do offer machine learning services including patient risk "
        "classification.",
    ),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_booking_redirect_is_removed(raw: str, expected: str) -> None:
    out = _strip_booking_redirect(raw)
    assert out.strip() == expected.strip()


def test_a_pure_redirect_is_left_alone() -> None:
    """If the whole reply IS the push, removing it would leave the customer
    with nothing; the caller substitutes the standard step prompt instead."""
    pure = "Would you like to book an appointment instead?"
    assert _strip_booking_redirect(pure) == pure


def test_a_real_answer_is_untouched() -> None:
    raw = "The capital of Japan is Tokyo."
    assert _strip_booking_redirect(raw) == raw


def test_booking_questions_are_not_mangled() -> None:
    """A question that is itself about the booking must survive, or the
    customer can never ask about rescheduling."""
    raw = "You can reschedule or cancel your booking any time before the visit."
    assert _strip_booking_redirect(raw) == raw


def test_empty_input_is_safe() -> None:
    assert _strip_booking_redirect("") == ""
    assert _strip_booking_redirect(None) == ""


def test_address_and_phone_numbers_survive() -> None:
    raw = "Our support number is +91 63500 63338. Please share a preferred date."
    out = _strip_booking_redirect(raw)
    assert "+91 63500 63338" in out
    assert "preferred date" not in out.lower()
