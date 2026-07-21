from app.engine.user_visible_text import extract_user_visible_text, sanitize_user_visible_text


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
