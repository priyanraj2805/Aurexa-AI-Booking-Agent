import pytest

from app.engine.state_merge import apply_state_merge


def test_multi_field_merge_persists_all_valid_fields_and_reports_deltas() -> None:
    result = apply_state_merge(
        current_fields={"name": "Ada"},
        extracted_fields={"email": "ada@test.com", "phone": "+15551234567"},
        required_fields=["name", "email", "phone"],
        optional_fields=[],
    )

    assert result["collected_fields"] == {
        "name": "Ada",
        "email": "ada@test.com",
        "phone": "+15551234567",
    }
    assert result["captured_deltas"] == {"email": "ada@test.com", "phone": "+15551234567"}
    assert result["missing_fields"] == []


def test_edit_patches_in_place_without_resetting_progress() -> None:
    result = apply_state_merge(
        current_fields={"name": "Ada", "email": "old@test.com", "phone": "+15551234567"},
        extracted_fields={"email": "new@test.com"},
        required_fields=["name", "email", "phone"],
        optional_fields=["notes"],
    )

    assert result["collected_fields"]["email"] == "new@test.com"
    assert result["missing_fields"] == []
    assert result["captured_deltas"] == {"email": "new@test.com"}


def test_optional_skip_marks_skip_once_and_suppresses_reask_loop() -> None:
    result = apply_state_merge(
        current_fields={"name": "Ada"},
        extracted_fields={},
        required_fields=["name"],
        optional_fields=["phone"],
        optional_skips=set(),
        skipped_fields=["phone"],
    )

    assert result["optional_skips"] == {"phone"}
    assert "phone" not in result["follow_up_fields"]
    assert result["missing_fields"] == []


def test_mixed_validity_persists_valid_subset_and_requests_follow_up_for_invalid_only() -> None:
    result = apply_state_merge(
        current_fields={"name": "Ada"},
        extracted_fields={"email": "ada@test.com", "phone": "abc"},
        required_fields=["name", "email", "phone"],
        optional_fields=[],
        invalid_fields={"phone": "must contain 10+ digits"},
    )

    assert result["collected_fields"]["email"] == "ada@test.com"
    assert result["collected_fields"].get("phone") is None
    assert result["follow_up_fields"] == ["phone"]
    assert result["diagnostics"]["invalid_fields"] == {"phone": "must contain 10+ digits"}


def test_same_turn_conflicts_require_clarification_and_block_overwrite() -> None:
    result = apply_state_merge(
        current_fields={"date": "2026-05-10"},
        extracted_fields={"date": "2026-05-11"},
        required_fields=["date"],
        optional_fields=[],
        conflicts={"date": ["2026-05-10", "2026-05-11"]},
    )

    assert result["collected_fields"]["date"] == "2026-05-10"
    assert result["pending_conflict"] == {"date": ["2026-05-10", "2026-05-11"]}
    assert result["follow_up_fields"] == ["date"]
    assert "date" not in result["captured_deltas"]


def test_unknown_fields_are_logged_and_do_not_block_progression() -> None:
    result = apply_state_merge(
        current_fields={"name": "Ada"},
        extracted_fields={"email": "ada@test.com", "favorite_color": "blue"},
        required_fields=["name", "email"],
        optional_fields=[],
    )

    assert result["collected_fields"] == {"name": "Ada", "email": "ada@test.com"}
    assert result["missing_fields"] == []
    assert result["diagnostics"]["unknown_fields"] == {"favorite_color": "blue"}
