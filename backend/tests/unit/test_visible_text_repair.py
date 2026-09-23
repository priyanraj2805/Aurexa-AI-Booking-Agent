"""Malformed model JSON must never leak into the chat as raw text."""
from app.engine.user_visible_text import (
    extract_user_visible_text,
    looks_like_json_debris,
)

# Exact production shape: opening brace missing.
MISSING_BRACE = (
    '"response_text": "That\'s fine. What date would you prefer for the appointment?",\n'
    '  "extracted_fields": {},\n'
    '  "reopen_fields": ["phone"],\n'
    '  "user_intent": "proceed",\n'
    '  "resistance_reason": "",\n'
    '  "confidence": 0.95\n'
    '}'
)


def test_missing_opening_brace_repaired():
    text, payload = extract_user_visible_text(MISSING_BRACE)
    assert text == "That's fine. What date would you prefer for the appointment?"
    assert payload.get("_repaired") is True
    assert "response_text" not in text
    assert "extracted_fields" not in text


def test_trailing_prose_after_json_parsed():
    raw = '{"response_text": "Hello there!", "confidence": 0.9} Hope this helps!'
    text, payload = extract_user_visible_text(raw)
    assert text == "Hello there!"


def test_fenced_json_still_parses():
    raw = '```json\n{"response_text": "Hi!", "confidence": 1.0}\n```'
    text, _ = extract_user_visible_text(raw)
    assert text == "Hi!"


def test_valid_json_unchanged():
    raw = '{"response_text": "Please choose a new date.", "confidence": 0.9}'
    text, payload = extract_user_visible_text(raw)
    assert text == "Please choose a new date."
    assert "_repaired" not in payload


def test_unrecoverable_debris_falls_back_safe():
    raw = '{"response_text": '
    text, _ = extract_user_visible_text(raw)
    assert text == "I'm here to help with your booking."
    assert "response_text" not in text


def test_debris_detector():
    assert looks_like_json_debris(MISSING_BRACE) is True
    assert looks_like_json_debris('{"response_text": "x"}') is True
    assert looks_like_json_debris("Please share your phone number.") is False
    assert looks_like_json_debris("") is False
    assert looks_like_json_debris(None) is False
