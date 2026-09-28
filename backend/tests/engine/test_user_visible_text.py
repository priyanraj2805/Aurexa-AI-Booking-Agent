from app.engine.user_visible_text import (
    extract_user_visible_text,
    looks_like_confirmation_claim,
    sanitize_user_visible_text,
)


def test_sanitize_user_visible_text_removes_think_tags() -> None:
    message = "<think>internal</think>Please pick a date."
    assert sanitize_user_visible_text(message) == "Please pick a date."


def test_sanitize_user_visible_text_blocks_reasoning_style_leakage() -> None:
    message = """
    Here is my analysis:
    1. First I'll reason internally.
    2. Then provide final answer.
    """
    assert sanitize_user_visible_text(message) == "I'm here to help with your booking."


def test_extract_user_visible_text_replaces_reasoning_only_response_text() -> None:
    raw = {
        "response_text": "Let's reason step by step before giving a final answer.",
        "other": "value",
    }

    text, payload = extract_user_visible_text(raw)

    assert text == "I'm here to help with your booking."
    assert payload["other"] == "value"


def test_extract_user_visible_text_preserves_booking_safe_structured_message() -> None:
    raw = '{"response_text":"Please choose a new date.","directive":"REQUIRE_DATE_PICKER"}'
    text, payload = extract_user_visible_text(raw)

    assert text == "Please choose a new date."
    assert payload["directive"] == "REQUIRE_DATE_PICKER"


def test_confirmation_claim_survives_an_inserted_date_and_time() -> None:
    """Regression: a slot detail between the noun and the verb defeated the
    truthfulness guard entirely.

    The model replied "Your booking for September 30, 2026 at 10:30 AM is
    confirmed." while no Booking row existed, because the pattern required the
    noun to sit next to the verb. Every extra clause is another way for a
    false confirmation to reach the customer.
    """
    assert looks_like_confirmation_claim(
        "Your booking for September 30 is confirmed."
    ) is True
    assert looks_like_confirmation_claim(
        "Your booking for September 30, 2026 at 10:30 AM is confirmed."
    ) is True
    assert looks_like_confirmation_claim(
        "The president of India is Droupadi Murmu. I am a booking assistant for "
        "Dotstark, so I can only help with appointments. Your booking for "
        "September 30, 2026 at 10:30 AM is confirmed."
    ) is True
    assert looks_like_confirmation_claim(
        "Your booking is still confirmed for September 30, 2026 at 10:30 AM."
    ) is True
    assert looks_like_confirmation_claim("Your slot at 10:30 AM has been booked.") is True


def test_confirmation_claim_still_ignores_questions_negations_and_requests() -> None:
    """The widened pattern must not turn prompts or denials into claims."""
    assert looks_like_confirmation_claim("Your booking is not confirmed yet.") is False
    assert looks_like_confirmation_claim("Is your booking for September 30 confirmed?") is False
    assert looks_like_confirmation_claim("Please confirm the booking to continue.") is False
    assert looks_like_confirmation_claim("I have not booked anything for you.") is False
    assert looks_like_confirmation_claim("Choose a date for your booking.") is False
    assert (
        looks_like_confirmation_claim(
            "Your booking has not been confirmed. Please enter the code."
        )
        is False
    )
