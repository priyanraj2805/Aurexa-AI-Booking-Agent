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


def test_fake_phone_sequential_walk_rejected():
    valid, errors = DataValidator.validate_entities({"phone": "1234567890"})
    assert "phone" not in valid
    assert "phone" in errors


def test_fake_phone_near_uniform_rejected():
    # The exact shape abused in prod (1 + zeros + 1): only 2 distinct digits.
    valid, errors = DataValidator.validate_entities({"phone": "1000000001"})
    assert "phone" not in valid
    assert "phone" in errors


def test_fake_phone_descending_walk_rejected():
    valid, errors = DataValidator.validate_entities({"phone": "9876543210"})
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


def test_name_keyboard_mashing_rejected():
    """The exact abuse from prod: 'Fhaaaaaaaa' / 'Heeee' must never validate."""
    for mash in ("Fhaaaaaaaa", "Heeee", "ookkkk", "Noheu29"):
        valid, errors = DataValidator.validate_entities({"name": mash})
        assert "name" not in valid, mash
        assert "name" in errors, mash


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


def _today_iso(tz="UTC"):
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).date().isoformat()


def test_same_day_denied_when_flag_off():
    result, error = DataValidator._validate_date(
        _today_iso(), "UTC", 90, allow_same_day_booking=False
    )
    assert result is None
    assert error is not None and "Same-day" in error


def test_same_day_allowed_when_flag_on():
    today = _today_iso()
    result, error = DataValidator._validate_date(
        today, "UTC", 90, allow_same_day_booking=True
    )
    assert result == today
    assert error is None


def test_future_date_valid_when_same_day_denied():
    future = (_utc_today() + timedelta(days=2)).isoformat()
    result, error = DataValidator._validate_date(
        future, "UTC", 90, allow_same_day_booking=False
    )
    assert result == future
    assert error is None


def test_validate_entities_same_day_denied_plain_route():
    valid, errors = DataValidator.validate_entities(
        {"date": _today_iso()},
        timezone="UTC",
        max_booking_days=90,
        allow_same_day_booking=False,
    )
    assert "date" not in valid
    assert "Same-day" in errors.get("date", "")


def test_validate_entities_same_day_denied_typed_route():
    fds = [
        FieldDefinition(name="date", type="date", label="Date", required=True, order=0)
    ]
    valid, errors = DataValidator.validate_entities(
        {"date": _today_iso()},
        field_definitions=fds,
        timezone="UTC",
        max_booking_days=90,
        allow_same_day_booking=False,
    )
    assert "date" not in valid
    assert "Same-day" in errors.get("date", "")


def test_validate_entities_same_day_default_stays_permissive():
    today = _today_iso()
    valid, errors = DataValidator.validate_entities({"date": today}, timezone="UTC")
    assert valid.get("date") == today
    assert errors == {}


def _utc_today():
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("UTC")).date()


def test_yesterday_resolves_to_past_date_and_is_refused():
    from app.engine.validators import _resolve_relative_date

    iso = _resolve_relative_date("yesterday", "UTC")
    assert iso == (_utc_today() + timedelta(days=-1)).isoformat()
    result, error = DataValidator._validate_date("yesterday", "UTC", 90)
    assert result is None
    assert error is not None and "past" in error.lower()


def test_day_before_yesterday_resolves():
    from app.engine.validators import _resolve_relative_date

    iso = _resolve_relative_date("day before yesterday", "UTC")
    assert iso == (_utc_today() + timedelta(days=-2)).isoformat()


def test_date_words_are_never_names():
    for word in ("yesterday", "today", "tomorrow", "next friday"):
        result, error = DataValidator._validate_name(word)
        assert result is None, word
        assert "date" in (error or "").lower(), word


def test_real_names_still_pass_including_may():
    for name in ("Rohan Sharma", "May", "Aarav"):
        result, error = DataValidator._validate_name(name)
        assert error is None, name
        assert result is not None
