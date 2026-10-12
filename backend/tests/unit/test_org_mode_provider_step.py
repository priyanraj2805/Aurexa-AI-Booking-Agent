"""Flow reorder: member choice comes AFTER the date.

- match_provider_selection resolves taps/numbers/names against the offered list.
- providers_for_date closed-day filtering is exercised live; here we assert
  the pure matcher contract the widget, WhatsApp and chat dispatch rely on.
"""

import sys

sys.path.insert(0, "backend")

from app.services.catalog_service import match_provider_selection

OFFERED = [
    {"id": "11111111-1111-1111-1111-111111111111", "full_name": "Dr Sharma"},
    {"id": "22222222-2222-2222-2222-222222222222", "full_name": "Dr Iyer"},
]


def _get(text):
    m = match_provider_selection(OFFERED, text)
    assert m is not None
    return m


def test_match_by_position():
    assert _get("1")["full_name"] == "Dr Sharma"
    assert _get("2")["full_name"] == "Dr Iyer"
    assert match_provider_selection(OFFERED, "3") is None
    assert match_provider_selection(OFFERED, "0") is None


def test_match_by_exact_id():
    assert _get("22222222-2222-2222-2222-222222222222")["full_name"] == "Dr Iyer"


def test_match_by_name_case_insensitive():
    assert _get("dr sharma")["id"] == OFFERED[0]["id"]
    assert _get("Dr Iyer")["id"] == OFFERED[1]["id"]


def test_match_unique_fragment():
    assert _get("iyer")["id"] == OFFERED[1]["id"]


def test_no_match_gibberish_or_empty():
    assert match_provider_selection(OFFERED, "hello there") is None
    assert match_provider_selection(OFFERED, "") is None
    assert match_provider_selection(OFFERED, "   ") is None
    assert match_provider_selection([], "1") is None


def test_ambiguous_fragment_returns_none():
    both = OFFERED + [{"id": "33333333-3333-3333-3333-333333333333", "full_name": "Dr Sharma Jr"}]
    # "sharma" matches two -> must NOT guess
    assert match_provider_selection(both, "sharma") is None
    # exact still works
    _exact = match_provider_selection(both, "Dr Sharma")
    assert _exact is not None and _exact["id"] == OFFERED[0]["id"]
