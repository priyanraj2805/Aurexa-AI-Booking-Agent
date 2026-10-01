"""The combiner must never send a call-to-action twice.

Production rows (all THREE shapes observed in one inbox):
  1. engine: "Is there anything I can help you book, check or reschedule?"
     + decline ending in the same sentence -> CTA twice.
  2. engine: "I don't have information about that. How about we finish up
     your booking instead?" + full decline.
  3. engine: "I can help you book an appointment though. Would you like to
     pick a time for October 8th?" + full decline.
"""
import pytest

from app.core.whatsapp_client import attach_knowledge_answer
from app.engine.user_visible_text import strip_booking_redirect


DECLINE = (
    "I'm a booking assistant, so I can only help with appointments, "
    "I don't have information about that.\n\n"
    "Is there anything I can help you book, check or reschedule?"
)


def test_lone_cta_answer_is_dropped() -> None:
    """Row 1: the engine message IS the push. Send the step body alone."""
    out = attach_knowledge_answer(
        "Is there anything I can help you book, check or reschedule?",
        DECLINE,
    )
    assert out == DECLINE
    assert out.count("Is there anything I can help you book") == 1


@pytest.mark.parametrize(
    ("push", "kept"),
    [
        ("How about we finish up your booking instead?",
         "Dotstark offers Azure development and support."),
        ("I can help you book an appointment though.",
         "Dotstark offers Azure development and support."),
        ("Would you like to pick a time for October 8th?",
         "Dotstark offers Azure development and support."),
        ("Back to your booking, could you please share a preferred date?",
         "The capital of Japan is Tokyo and it is quite large."),
        ("Would you like to book an appointment instead?",
         "The capital of Japan is Tokyo and it is quite large."),
        ("Is there anything I can help you book, check or reschedule?",
         "The capital of Japan is Tokyo and it is quite large."),
    ],
)
def test_push_sentences_are_recognised(push: str, kept: str) -> None:
    assert strip_booking_redirect(f"{kept} {push}") == kept


def test_combiner_keeps_a_real_answer() -> None:
    """A substantive answer plus a step must both survive, once each."""
    out = attach_knowledge_answer(
        "The capital of Japan is Tokyo.",
        "What date would you like to book?",
    )
    assert "Tokyo" in out
    assert "What date" in out


def test_identical_inputs_collapse() -> None:
    out = attach_knowledge_answer(DECLINE, DECLINE)
    assert out == DECLINE


def test_empty_sides_fall_back() -> None:
    assert attach_knowledge_answer("", DECLINE) == DECLINE
    assert attach_knowledge_answer("Hello", "") == "Hello"


def test_no_double_cta_in_any_combination() -> None:
    """The invariant from the screenshot: the CTA sentence appears once."""
    answers = [
        "Is there anything I can help you book, check or reschedule?",
        "Tokyo. Is there anything I can help you book, check or reschedule?",
        "I can help you book an appointment though.",
    ]
    for ans in answers:
        out = attach_knowledge_answer(ans, DECLINE)
        assert out.count("Is there anything I can help you book") <= 1, ans
