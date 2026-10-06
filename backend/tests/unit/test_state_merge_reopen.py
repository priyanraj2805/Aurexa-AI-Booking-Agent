"""Reopen keeps the stored value until a fresh one arrives.

Production: "change my number to 9814712345" was acknowledged, but the
old number silently survived — the reopen popped it in memory while the
DB merge resurrected it on the next turn, so the correction was lost.
"""

from app.engine.state_merge import apply_state_merge

REQUIRED = ["name", "email", "phone", "date", "time"]


def _base(extracted=None, reopen=None):
    return apply_state_merge(
        current_fields={
            "name": "Rohan Sharma",
            "email": "rohan.sharma@example.com",
            "phone": "+919822047136",
            "date": "2026-10-06",
            "time": "3:00 PM",
        },
        extracted_fields=extracted or {},
        required_fields=REQUIRED,
        optional_fields=[],
        invalid_fields={},
        reopen_fields=reopen,
    )


def test_reopen_keeps_old_value_and_reasks():
    out = _base(reopen=["phone"])
    assert out["collected_fields"]["phone"] == "+919822047136"
    assert out["follow_up_fields"][0] == "phone"


def test_reopen_with_fresh_value_saves_and_does_not_reask():
    out = _base(
        extracted={"phone": "+919814712345"},
        reopen=["phone"],
    )
    assert out["collected_fields"]["phone"] == "+919814712345"
    assert "phone" not in out["follow_up_fields"]
    assert out["follow_up_fields"] == []
