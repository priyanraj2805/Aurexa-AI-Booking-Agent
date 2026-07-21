from app.engine.validators import DataValidator
from app.schemas.agent import FieldDefinition
from datetime import datetime, timedelta


def test_valid_email():
    valid, errors = DataValidator.validate_entities({"email": "test@example.com"})
    assert "email" in valid
    assert len(errors) == 0


def test_invalid_email():
    valid, errors = DataValidator.validate_entities({"email": "test@com"})
    assert "email" not in valid
    assert "email" in errors


def test_invalid_email_spaces():
    valid, errors = DataValidator.validate_entities({"email": "te st@example.com"})
    assert "email" not in valid


def test_valid_phone():
    valid, errors = DataValidator.validate_entities({"phone": "(555) 123-4567"})
    assert "phone" in valid
    assert len(errors) == 0


def test_short_phone():
    valid, errors = DataValidator.validate_entities({"phone": "123-4567"})
    assert "phone" not in valid
    assert "phone" in errors


def test_fake_phone_all_same():
    valid, errors = DataValidator.validate_entities({"phone": "1111111111"})
    assert "phone" not in valid
    assert "phone" in errors


def test_past_date():
    valid, errors = DataValidator.validate_entities({"date": "2020-01-01"})
    assert "date" not in valid
    assert "date" in errors


def test_future_date_within_bound():
    future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    valid, errors = DataValidator.validate_entities({"date": future})
    assert "date" in valid
    assert len(errors) == 0


def test_future_date_beyond_bound():
    far_future = (datetime.now() + timedelta(days=200)).strftime("%Y-%m-%d")
    valid, errors = DataValidator.validate_entities({"date": far_future})
    assert "date" not in valid
    assert "date" in errors


def test_long_string():
    long_string = "A" * 300
    valid, errors = DataValidator.validate_entities({"name": long_string})
    assert "name" not in valid
    assert "name" in errors


def test_na_value_rejected():
    valid, errors = DataValidator.validate_entities({"name": "n/a"})
    assert "name" not in valid
    assert "name" in errors


def test_none_value_rejected():
    valid, errors = DataValidator.validate_entities({"name": "none"})
    assert "name" not in valid
    assert "name" in errors


def test_null_value_rejected():
    valid, errors = DataValidator.validate_entities({"email": "null"})
    assert "email" not in valid
    assert "email" in errors


def test_empty_string_rejected():
    valid, errors = DataValidator.validate_entities({"name": ""})
    assert "name" not in valid


def test_whitespace_value_rejected():
    valid, errors = DataValidator.validate_entities({"name": "   "})
    assert "name" not in valid


# ── Name validation tests ─────────────────────────────────
def test_valid_name():
    valid, errors = DataValidator.validate_entities({"name": "Devang Sharma"})
    assert valid["name"] == "Devang Sharma"
    assert "name" not in errors


def test_name_with_garbage_numbers():
    """Reject names that are mostly numbers/symbols like 'Devang 289ye8gdedg 23y'"""
    valid, errors = DataValidator.validate_entities({"name": "D23234"})
    assert "name" not in valid
    assert "name" in errors


def test_name_with_mixed_garbage():
    valid, errors = DataValidator.validate_entities({"name": "Devang 289ye8gdedg 23y"})
    assert "name" not in valid
    assert "name" in errors


def test_name_too_short():
    valid, errors = DataValidator.validate_entities({"name": "A"})
    assert "name" not in valid
    assert "name" in errors


def test_name_hyphenated_valid():
    valid, errors = DataValidator.validate_entities({"name": "Mary-Jane O'Brien"})
    assert "name" in valid
    assert valid["name"] == "Mary-Jane O'Brien"


def test_name_with_dots_valid():
    valid, errors = DataValidator.validate_entities({"name": "Dr. Smith Jr."})
    assert "name" in valid


def test_time_rejects_plain_greeting_text():
    valid, errors = DataValidator.validate_entities({"time": "HI"})
    assert "time" not in valid
    assert "time" in errors


def test_time_accepts_normal_clock_format():
    valid, errors = DataValidator.validate_entities({"time": "1:30 PM"})
    assert "time" in valid
    assert valid["time"].startswith("13:30")
    assert "time" not in errors


def test_time_rejects_date_only_string_to_prevent_midnight_backfill():
    valid, errors = DataValidator.validate_entities({"time": "2026-04-01"})
    assert "time" not in valid
    assert "time" in errors


def test_phone_country_aware_accepts_valid_and_rejects_invalid_phone_policy():
    field_definitions = [
        FieldDefinition(
            name="phone",
            type="phone",
            label="Phone",
            required=True,
            order=0,
        )
    ]
    valid, errors, metadata = DataValidator.validate_entities(
        {"phone": "415-555-2671"},
        field_definitions=field_definitions,
        field_meta={"phone": {"country_code": "US"}},
        include_metadata=True,
    )
    assert valid["phone"] == "+14155552671"
    assert "phone" not in errors
    assert metadata["phone"]["country_code"] == "US"
    assert metadata["phone"]["raw_input"] == "415-555-2671"

    valid_bad, errors_bad, _ = DataValidator.validate_entities(
        {"phone": "12345"},
        field_definitions=field_definitions,
        field_meta={"phone": {"country_code": "GB"}},
        include_metadata=True,
    )
    assert "phone" not in valid_bad
    assert "phone" in errors_bad


def test_phone_validation_returns_country_code_and_raw_input_metadata_phone():
    field_definitions = [
        FieldDefinition(
            name="phone",
            type="phone",
            label="Phone",
            required=True,
            order=0,
        )
    ]
    valid, errors, metadata = DataValidator.validate_entities(
        {"phone": "020 7946 0018"},
        field_definitions=field_definitions,
        field_meta={"phone": {"country_code": "GB"}},
        include_metadata=True,
    )
    assert "phone" in valid
    assert errors == {}
    assert metadata["phone"]["country_code"] == "GB"
    assert metadata["phone"]["raw_input"] == "020 7946 0018"
