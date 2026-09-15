"""Tests for booking-flow guards: greeting-only detection, next-field cue check,
and prose-wrapped JSON salvage pattern used by the orchestrator."""

from app.engine.message_processor import (
    _is_greeting_only,
    _is_reserved_name_value,
    _field_request_cues,
    _response_asks_for_field,
)


class TestIsGreetingOnly:
    def test_bare_greetings(self):
        for text in ["hi", "Hii..", "HII", "hello!", "Hey", "yo.", "namaste"]:
            assert _is_greeting_only(text), text

    def test_not_greetings(self):
        for text in [
            "Priyanshu",
            "Hi, I am Priyanshu",
            "Hii, book an appointment",
            "nishantraj785908@gmail.com",
            "9876543210",
            "",
            "ok thanks bye",
        ]:
            assert not _is_greeting_only(text), text


class TestResponseAsksForField:
    def test_email_cues(self):
        assert _response_asks_for_field(
            "Please share your email address.", "email", "Email Address"
        )
        assert not _response_asks_for_field(
            "Please share your email address.", "phone", "Phone Number"
        )

    def test_turn3_regression(self):
        # Collected email already; missing phone. Model prose re-asks email:
        # the guard must report that phone was NOT asked for.
        prose = "Thank you, Hii... Please share your email address so I can send you a verification code."
        assert not _response_asks_for_field(prose, "phone", "Phone Number")
        assert _response_asks_for_field(prose, "email", "Email Address")

    def test_date_time_cues(self):
        assert _response_asks_for_field(
            "Which day works for you?", "date", "Preferred Date"
        )
        assert _response_asks_for_field(
            "Pick a time slot below.", "time", "Preferred Time"
        )
        assert not _response_asks_for_field("Pick a time slot below.", "date", "Date")

    def test_custom_field_label_words(self):
        assert _response_asks_for_field(
            "Please choose your Organisation Size.", "org_size", "Organisation Size"
        )

    def test_empty_inputs(self):
        assert not _response_asks_for_field("", "email", "Email")
        assert not _response_asks_for_field("Share your email.", "", None)


class TestReservedNameValues:
    def test_field_words_rejected(self):
        for value in ["Email", "email", "PHONE", "Date?", "otp.", "Booking!"]:
            assert _is_reserved_name_value(value), value

    def test_real_names_accepted(self):
        for value in ["Priyanshu", "Nishant Arya", "John Doe", "Aarav"]:
            assert not _is_reserved_name_value(value), value


class TestFieldRequestCues:
    def test_builtin_cues_present(self):
        assert "verification code" in _field_request_cues("email", "Email")
        assert "whatsapp" in _field_request_cues("phone", "Phone")
        assert "slot" in _field_request_cues("time", "Time")
