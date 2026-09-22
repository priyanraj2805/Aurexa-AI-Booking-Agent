"""OTP verify-turn determinism + verified-phone backfill (screenshot regression).

TranscriptMirror: OTP sent to collected number -> user enters 6-digit code ->
collected phone missing from the turn snapshot. The code MUST still be
verification-attempted (never reinterpreted as a field value), and a
successful phone verification MUST backfill the normalized number so the
booking can never observe "verified but missing".
"""
from app.engine import otp_guards as orch
from app.engine import message_processor as mp


def test_resolve_channels_attempts_without_collected_phone():
    # OTP was sent, phone required+unverified, snapshot lost the number:
    # phone channel must still be attempted (the screenshot bug).
    channels = orch.resolve_otp_verify_channels(
        is_whatsapp=True,
        require_phone_otp=True,
        require_otp=False,
        phone_verified=False,
        email_verified=False,
        collected_phone="",
        otp_was_sent=True,
    )
    assert channels == ["phone"]


def test_resolve_channels_both_pending_whatsapp_order():
    channels = orch.resolve_otp_verify_channels(
        is_whatsapp=True,
        require_phone_otp=True,
        require_otp=True,
        phone_verified=False,
        email_verified=False,
        collected_phone="+911234567890",
        otp_was_sent=True,
    )
    assert channels == ["phone", "email"]


def test_resolve_channels_respects_verified_flags():
    channels = orch.resolve_otp_verify_channels(
        is_whatsapp=True,
        require_phone_otp=True,
        require_otp=False,
        phone_verified=True,
        email_verified=False,
        collected_phone="+911234567890",
        otp_was_sent=True,
    )
    assert channels == []


def test_resolve_channels_no_send_no_attempt():
    # No OTP ever sent -> legacy behavior preserved (no blind attempts).
    channels = orch.resolve_otp_verify_channels(
        is_whatsapp=True,
        require_phone_otp=True,
        require_otp=False,
        phone_verified=False,
        email_verified=False,
        collected_phone="",
        otp_was_sent=False,
    )
    assert channels == []


def test_apply_verified_phone_backfills_missing():
    collected: dict = {"name": "Virender"}
    out = orch.apply_verified_phone(collected, "+91 63678 94375")
    assert out["phone"] == "+916367894375"


def test_apply_verified_phone_keeps_valid_existing_on_garbage():
    collected = {"phone": "+916367894375"}
    out = orch.apply_verified_phone(collected, "not-a-number!!!")
    assert out["phone"] == "+916367894375"


def test_time_step_echoes_selected_iso_date():
    msg = mp._build_context_aware_response(
        directive="REQUIRE_TIME_SLOT_GRID",
        field_meta={"name": "time"},
        collected_data={"name": "Devang", "date": "2026-09-25"},
        valid_data={},
        errors={},
        llm_generated_msg="",
        agent_name="Booking Assistant",
    )
    assert "2026-09-25" in msg
    assert "September 25, 2026" in msg


def test_time_step_echo_without_date_falls_back():
    msg = mp._build_context_aware_response(
        directive="REQUIRE_TIME_SLOT_GRID",
        field_meta={"name": "time"},
        collected_data={},
        valid_data={},
        errors={},
        llm_generated_msg="",
        agent_name="Booking Assistant",
    )
    assert "2026" not in msg
    assert "available slots" in msg.lower()
