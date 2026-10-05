"""Prompt-instruction echo must never reach a customer.

Production failure: the model echoed part of the BUSINESS HOURS prompt
block ("Answer questions about opening hours, timings, availability
windows or ...") into response_message, glued to the real reply
("Thanks! What date would you like to book for?"). The header guard only
checked the first line and the JSON guard only handled JSON shapes, so
mid-text prose echo passed straight through to WhatsApp.
"""
import pytest

from app.core.wa_text import format_outbound, strip_leaked_headers
from app.engine.user_visible_text import (
    extract_user_visible_text,
    strip_instruction_echo,
)


def test_production_echo_shape_keeps_human_reply() -> None:
    raw = (
        "Answer questions about opening hours, timings, availability windows or "
        "booking rules directly from this block. Only start the booking flow when "
        "the user actually wants to book an appointment. "
        "Thanks! What date would you like to book for?"
    )
    message, _ = extract_user_visible_text(raw)
    assert "availability windows" not in message
    assert "booking flow" not in message
    assert message == "Thanks! What date would you like to book for?"


def test_full_echo_falls_back_never_leaks() -> None:
    raw = (
        "Answer questions about opening hours, timings, availability windows or "
        "booking rules directly from this block."
    )
    message, _ = extract_user_visible_text(raw)
    assert "availability windows" not in message
    assert "BUSINESS" not in message


def test_legit_hours_question_is_untouched() -> None:
    for legit in (
        "What are your business hours?",
        "Here are our availability windows for tomorrow: 10am, 2pm.",
        "Thanks! What date would you like to book?",
    ):
        assert strip_instruction_echo(legit) == legit


def test_machine_vocab_sentences_are_cut() -> None:
    raw = (
        "Our shop opens at 9am. Per SESSION CONTEXT the missing field is phone. "
        "Could you please share your phone number?"
    )
    cleaned = strip_instruction_echo(raw)
    assert "SESSION CONTEXT" not in cleaned
    assert "Could you please share your phone number?" in cleaned


def test_mid_text_known_header_is_stripped() -> None:
    raw = "Our timings are 9am to 6pm.\nBUSINESS HOURS\nMon-Fri 9-6."
    cleaned = strip_leaked_headers(raw)
    assert "BUSINESS HOURS" not in cleaned
    assert "Our timings are 9am to 6pm." in cleaned


def test_header_variants_are_stripped() -> None:
    for variant in (
        "## BUSINESS HOURS",
        "**BUSINESS HOURS**",
        "Business Hours:",
        "BUSINESS HOURS (authoritative, answer timing questions from this)",
    ):
        cleaned = strip_leaked_headers(f"{variant}\nWe open at 9am.")
        assert "BUSINESS" not in cleaned.upper()
        assert "We open at 9am." in cleaned


def test_unknown_all_caps_is_left_alone() -> None:
    raw = "HELLO there, how can I help?"
    assert strip_leaked_headers(raw) == raw


def test_false_acceptance_claims_are_detected() -> None:
    """Production: "Thanks! I've got your number..." was sent while the
    phone had FAILED validation and was never saved. The customer believed
    the number was done, never re-sent it, and the bot asked for it forever.
    The old verb list (saved/stored/...) missed "got" entirely."""
    from app.engine.user_visible_text import looks_like_field_saved_claim

    for claim in (
        "Thanks! I've got your number. When would you like to book?",
        "Got it. To move forward, could you please share your phone number?",
        "I have received your email, thanks!",
    ):
        assert looks_like_field_saved_claim(claim, "phone") is True


def test_ordinary_questions_are_not_saved_claims() -> None:
    from app.engine.user_visible_text import looks_like_field_saved_claim

    for text in (
        "What is your phone number?",
        "Could you please share your phone number?",
        "I have sent the OTP to your phone. Please enter it here.",
        "I have a question about your phone policy.",
    ):
        assert looks_like_field_saved_claim(text, "phone") is False


def test_format_outbound_end_to_end() -> None:
    raw = (
        "## BUSINESS HOURS (authoritative — answer timing questions from this)\n"
        "Answer questions about opening hours, timings, availability windows or "
        "booking rules directly from this block. "
        "Thanks! What date would you like to book for?"
    )
    out = format_outbound(raw)
    assert "BUSINESS" not in out.upper()
    assert "availability windows or" not in out
    assert "What date would you like to book for?" in out
