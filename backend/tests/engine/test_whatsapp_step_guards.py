"""Strict WhatsApp step guards — the "2" -> 19 September regression.

Covers app.engine.whatsapp_guards (pure, no DB):
- bare numbers are NEVER dates (the reported diversion);
- option text matching has no positional index for dates;
- slot index shortcut preserved for times;
- corrections name exact formats and never contain numbered pick-lists.
"""
from datetime import date, timedelta

from app.engine import whatsapp_guards as guards


def _iso_plus(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_bare_number_is_never_a_date():
    iso, correction, reason = guards.validate_typed_date(
        "2", tz="UTC", max_days=30, examples=["18/09/2026", "19/09/2026"]
    )
    assert iso is None
    assert reason == "bare_number"
    assert correction is not None
    # Live examples echoed — never a hardcoded sample.
    assert "18/09/2026" in correction and "19/09/2026" in correction


def test_bare_number_with_spaces_rejected():
    iso, _, reason = guards.validate_typed_date(
        "  7 ", tz="UTC", max_days=30, examples=["18/09/2026"]
    )
    assert iso is None
    assert reason == "bare_number"


def test_valid_future_date_accepted():
    iso, correction, reason = guards.validate_typed_date(
        _iso_plus(5), tz="UTC", max_days=30
    )
    assert reason == "ok"
    assert correction is None
    assert iso == _iso_plus(5)


def test_past_date_rejected_with_format():
    iso, correction, reason = guards.validate_typed_date(
        "2000-01-01", tz="UTC", max_days=30, examples=["18/09/2026"]
    )
    assert iso is None
    assert reason == "past"
    assert correction is not None
    assert "18/09/2026" in correction


def test_far_future_date_rejected():
    iso, correction, reason = guards.validate_typed_date(
        _iso_plus(400), tz="UTC", max_days=30
    )
    assert iso is None
    assert reason == "too_far"


def test_gibberish_rejected():
    iso, correction, reason = guards.validate_typed_date(
        "someday maybe", tz="UTC", max_days=30, examples=["18/09/2026"]
    )
    assert iso is None
    assert reason == "invalid"
    assert correction is not None
    assert "18/09/2026" in correction


def test_date_option_match_has_no_index():
    opts = [
        {"id": "date_20260918", "label": "Thu, Sep 18", "value": "2026-09-18"},
        {"id": "date_20260919", "label": "Fri, Sep 19", "value": "2026-09-19"},
    ]
    # Exact label/value still match (case-insensitive, spaceless).
    m1 = guards.match_option_by_text(opts, "Fri, Sep 19")
    assert m1 is not None and m1["value"] == "2026-09-19"
    m2 = guards.match_option_by_text(opts, "2026-09-18")
    assert m2 is not None and m2["value"] == "2026-09-18"
    m3 = guards.match_option_by_text(opts, "fri,sep19")
    assert m3 is not None and m3["value"] == "2026-09-19"
    # But "2" must NOT resolve to the 2nd hidden option (the reported bug).
    assert guards.match_option_by_text(opts, "2") is None
    assert guards.match_option_by_text(opts, "1") is None


def test_slot_index_shortcut_preserved():
    opts = [
        {"id": "s1", "label": "3:00 PM", "value": "3:00 PM"},
        {"id": "s2", "label": "3:30 PM", "value": "3:30 PM"},
    ]
    s2 = guards.match_slot_index(opts, "2")
    assert s2 is not None and s2["id"] == "s2"
    assert guards.match_slot_index(opts, "99") is None
    assert guards.match_slot_index(opts, "3 PM") is None


def test_confirm_intent_mapping():
    assert guards.match_confirm_intent("CONFIRM") == "confirm"
    assert guards.match_confirm_intent("yes, confirm booking") == "confirm"
    assert guards.match_confirm_intent("change my date") == "change"
    assert guards.match_confirm_intent("I want to edit details") == "change"
    assert guards.match_confirm_intent("hello there") is None


def test_corrections_name_formats_not_numbers():
    _, correction, _ = guards.validate_typed_date(
        "2", tz="UTC", max_days=30, examples=["18/09/2026", "19/09/2026"]
    )
    assert correction is not None
    date_body = guards.date_correction("2", correction, "We are available from X to Y.")
    assert "18/09/2026" in date_body
    assert "X to Y" in date_body  # horizon context retained
    # No numbered pick-list lines like "1. ..." / "2. ...".
    import re

    assert not re.search(r"(?m)^\d+\.\s", date_body)

    time_body = guards.time_correction("xyz", "Available times.", examples=["3:00 PM", "3:30 PM"])
    assert "3:00 PM" in time_body
    assert not re.search(r"(?m)^\d+\.\s", time_body)

    assert "CONFIRM" in guards.confirm_correction()
    assert "CHANGE" in guards.confirm_correction()


def test_example_hints_come_from_live_data():
    # Real option ISOs become typed-format examples — no literals involved.
    hints = guards.example_date_hints(["2026-09-18", "2026-09-19"], tz="UTC")
    assert hints == ["18/09/2026", "19/09/2026"]
    # Empty options fall back to computed near-future dates, never literals.
    fallback = guards.example_date_hints([], tz="UTC")
    assert len(fallback) == 2
    assert all(__import__("re").fullmatch(r"\d{2}/\d{2}/\d{4}", h) for h in fallback)
    # Time hints are just the live slot labels.
    assert guards.example_time_hints(["3:00 PM", "3:30 PM"]) == ["3:00 PM", "3:30 PM"]
    assert guards.example_time_hints([]) == []


def test_no_hardcoded_sample_dates_in_guards():
    """The guard module must not contain literal sample dates/times.

    Only CONFIRM/CHANGE stay literal — they are protocol command words,
    not examples.
    """
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "app" / "engine" / "whatsapp_guards.py"
    text = src.read_text(encoding="utf-8")
    for literal in ("19 Sep", "19/09/2026", "18 Sep", "18/09/2026", "3 PM", "15:30"):
        assert literal not in text, f"hardcoded sample leaked: {literal}"


def test_cancel_bypass_never_trapped():
    assert guards.is_cancel_bypass("cancel") is True
    assert guards.is_cancel_bypass("please cancel my booking") is True
    assert guards.is_cancel_bypass("stop") is True
    assert guards.is_cancel_bypass("3 PM") is False
    assert guards.is_cancel_bypass("confirm") is False
    assert guards.is_cancel_bypass("19 Sep") is False


def test_field_change_routing():
    collected = {"name": "Asha", "date": "2026-09-19", "time": "3:00 PM"}
    # Different collected field than the current step -> route to engine.
    assert (
        guards.detect_field_change_request("change the date", collected, "time")
        == "date"
    )
    assert (
        guards.detect_field_change_request("change my phone to 98", collected, "time")
        is None  # phone not collected -> normal validation path
    )
    collected2 = dict(collected, phone="+919999999999")
    assert (
        guards.detect_field_change_request("wrong phone, use 98", collected2, "date")
        == "phone"
    )
    # Current step's own field is validated normally, never rerouted.
    assert (
        guards.detect_field_change_request("change the date", collected, "date")
        is None
    )
    # Chit-chat is not a change request.
    assert guards.detect_field_change_request("hello there", collected, "time") is None


def test_apply_field_change_clears_dependents():
    collected = {
        "name": "Asha",
        "date": "2026-09-19",
        "time": "3:00 PM",
        "selected_slot_id": "abc",
        "locked_slot_id": "abc",
        "locked_slot_start_utc": "x",
    }
    out = guards.apply_collected_field_change(collected, "date")
    assert "date" not in out
    assert "time" not in out  # time picked for the old date is dropped
    assert "selected_slot_id" not in out
    assert "locked_slot_id" not in out
    assert out["name"] == "Asha"  # personal data untouched

    out2 = guards.apply_collected_field_change(collected, "phone")
    assert out2["date"] == "2026-09-19"  # unrelated fields kept
    assert "phone" not in out2
