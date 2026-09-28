"""Tests for WhatsApp directive rendering (widget parity on WhatsApp)."""

from types import SimpleNamespace

from dateutil.parser import parse as date_parse

from app.core.whatsapp_client import format_action_for_whatsapp
from app.engine.message_processor import _upcoming_date_options


def _action(directive, message="Pick one", field_meta=None):
    return SimpleNamespace(
        widget_directive=directive, message=message, field_meta=field_meta
    )


class TestTimeSlotList:
    def test_grid_with_options_becomes_list(self):
        out = format_action_for_whatsapp(
            _action(
                "REQUIRE_TIME_SLOT_GRID",
                "Choose a time",
                {"options": ["10:00 AM", "10:30 AM", "11:00 AM"]},
            )
        )
        assert out["type"] == "interactive_list"
        assert out["button_text"] == "Choose time"
        rows = out["sections"][0]["rows"]
        assert [r["title"] for r in rows] == ["10:00 AM", "10:30 AM", "11:00 AM"]
        # Row ids carry the same parseable text (inbound extracts titles).
        assert all(r["id"] == r["title"] for r in rows)

    def test_grid_caps_at_ten_rows_with_hint(self):
        opts = [f"{h}:00 AM" for h in range(1, 13)]
        out = format_action_for_whatsapp(
            _action("REQUIRE_TIME_SLOT_GRID", "Choose", {"options": opts})
        )
        assert len(out["sections"][0]["rows"]) == 10
        assert "more" in out["body"]

    def test_grid_without_options_stays_text(self):
        out = format_action_for_whatsapp(
            _action("REQUIRE_TIME_SLOT_GRID", "Choose", {"options": []})
        )
        assert out == {"type": "text", "body": "Choose"}
        out2 = format_action_for_whatsapp(_action("REQUIRE_TIME_SLOT_GRID", "Choose", None))
        assert out2 == {"type": "text", "body": "Choose"}


class TestDateList:
    def test_date_with_options_becomes_list(self):
        out = format_action_for_whatsapp(
            _action(
                "REQUIRE_DATE_PICKER",
                "Choose a date",
                {"options": ["Sat, Sep 13", "Sun, Sep 14"]},
            )
        )
        assert out["type"] == "interactive_list"
        assert out["button_text"] == "Choose date"
        assert out["sections"][0]["title"] == "Upcoming days"

    def test_date_without_options_stays_text(self):
        out = format_action_for_whatsapp(_action("REQUIRE_DATE_PICKER", "Choose", None))
        assert out == {"type": "text", "body": "Choose"}


class TestUnchangedDirectives:
    def test_confirm_still_buttons(self):
        out = format_action_for_whatsapp(
            _action("REQUEST_CONFIRMATION", "Confirm?")
        )
        assert out["type"] == "interactive_buttons"
        assert len(out["buttons"]) == 3

    def test_otp_still_text(self):
        out = format_action_for_whatsapp(_action("REQUIRE_OTP_MODAL", "Code?"))
        assert out["type"] == "text"
        assert "verification code" in out["body"]

    def test_plain_text_passthrough(self):
        out = format_action_for_whatsapp(_action("TEXT_INPUT", "Hello"))
        assert out == {"type": "text", "body": "Hello"}


class TestUpcomingDateOptions:
    def test_seven_parseable_ascending_days(self):
        opts = _upcoming_date_options("Asia/Calcutta", 90)
        assert len(opts) == 7
        assert len(set(opts)) == 7
        parsed = [date_parse(o).date() for o in opts]
        assert parsed == sorted(parsed)

    def test_respects_max_booking_days(self):
        assert len(_upcoming_date_options("UTC", 2)) == 3  # today + 2

    def test_bad_timezone_falls_back_to_utc(self):
        assert len(_upcoming_date_options("Not/AZone", 90)) == 7


class TestDirectiveFrameMatchesAction:
    def test_confirm_frame_only_for_confirm_step_replies(self):
        from app.engine.booking_directive.service import directive_frame_matches_action

        assert directive_frame_matches_action("CONFIRM_BOOKING", "REQUEST_CONFIRMATION") is True
        assert directive_frame_matches_action("CONFIRM_BOOKING", "PROCESS_BOOKING") is True
        assert directive_frame_matches_action("CONFIRM_BOOKING", "BOOKING_CONFIRMED_CARD") is True
        assert directive_frame_matches_action("CONFIRM_BOOKING", "TEXT_INPUT") is False
        assert directive_frame_matches_action("CONFIRM_BOOKING", "REQUIRE_OTP_MODAL") is False
        assert directive_frame_matches_action("CONFIRM_BOOKING", None) is False

    def test_step_frames_match_their_widgets(self):
        from app.engine.booking_directive.service import directive_frame_matches_action

        assert directive_frame_matches_action("SELECT_DATE", "REQUIRE_DATE_PICKER") is True
        assert directive_frame_matches_action("SELECT_SLOT", "REQUIRE_TIME_SLOT_GRID") is True
        assert directive_frame_matches_action("SELECT_TIME", "REQUIRE_TIME_SLOT_GRID") is True
        assert directive_frame_matches_action("SELECT_DATE", "TEXT_INPUT") is False
        assert directive_frame_matches_action("BOGUS_TYPE", "TEXT_INPUT") is False
