"""Machine JSON must never render verbatim to a customer.

Production failure: the model returned an array-shaped payload
('[\\n "response_text": ..., "extracted_fields": {...}]' -- opening brace
replaced by a bracket) and the customer received the raw structure,
because only an inner extracted_fields fragment parsed and the code
returned the raw text whenever *something* had parsed.
"""
import pytest

from app.engine.user_visible_text import extract_user_visible_text


def test_malformed_array_payload_salvages_the_reply() -> None:
    """The exact production shape: repair extracts the human reply."""
    raw = (
        '[\n  "response_text": "Got it. Could you please share your phone number?",\n'
        '  "extracted_fields": {\n    "email": "arjun.nair@example.com"\n  },\n'
        '  "reopen_fields": []'
    )
    message, payload = extract_user_visible_text(raw)
    assert message == "Got it. Could you please share your phone number?"
    assert "response_text" not in message
    assert "extracted_fields" not in message


def test_fragment_without_reply_key_never_renders_raw() -> None:
    """A parsed fragment with no human reply inside must fall back,
    not render the machine structure."""
    raw = '[\n  "extracted_fields": {"email": "a@b.com"}\n]'
    message, _ = extract_user_visible_text(raw)
    assert "extracted_fields" not in message
    assert "{" not in message and "[" not in message


def test_dict_payload_without_reply_never_renders_repr() -> None:
    message, _ = extract_user_visible_text(
        {"extracted_fields": {"email": "a@b.com"}}
    )
    assert "extracted_fields" not in message
    assert "{" not in message


def test_unparseable_brace_text_never_renders_raw() -> None:
    message, _ = extract_user_visible_text('{"response_text": broken,,,')
    assert "response_text" not in message
    assert "{" not in message


def test_valid_full_object_still_extracts() -> None:
    message, payload = extract_user_visible_text(
        '{"response_text": "Hello there!", "user_intent": "proceed"}'
    )
    assert message == "Hello there!"
    assert payload.get("user_intent") == "proceed"


def test_plain_prose_passes_through_untouched() -> None:
    message, _ = extract_user_visible_text(
        "Thanks! What date would you like to book?"
    )
    assert message == "Thanks! What date would you like to book?"


def test_empty_and_junk_input_is_safe() -> None:
    message, _ = extract_user_visible_text("")
    assert "{" not in message and "[" not in message
    message, _ = extract_user_visible_text(None)
    assert "{" not in message
