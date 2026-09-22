from types import SimpleNamespace

from app.engine.cancel_flow import (
    _otp_channel_label,
    _resolve_cancel_otp_channel,
)


def _cfg(require_otp=False, require_phone_otp=False):
    return SimpleNamespace(require_otp=require_otp, require_phone_otp=require_phone_otp)


class TestResolveCancelOtpChannel:
    def test_whatsapp_phone_otp_enabled(self):
        assert _resolve_cancel_otp_channel(_cfg(require_phone_otp=True), "whatsapp") == "phone"  # type: ignore[arg-type]

    def test_whatsapp_phone_otp_enabled_cloud(self):
        assert _resolve_cancel_otp_channel(_cfg(require_phone_otp=True), "whatsapp_cloud") == "phone"  # type: ignore[arg-type]

    def test_email_otp_only(self):
        assert _resolve_cancel_otp_channel(_cfg(require_otp=True), "whatsapp") == "email"  # type: ignore[arg-type]

    def test_phone_otp_only_widget(self):
        assert _resolve_cancel_otp_channel(_cfg(require_phone_otp=True), "web") == "phone"  # type: ignore[arg-type]

    def test_phone_otp_only_cloud_channel_empty(self):
        assert _resolve_cancel_otp_channel(_cfg(require_phone_otp=True), "") == "phone"  # type: ignore[arg-type]

    def test_neither_enabled_returns_empty(self):
        assert _resolve_cancel_otp_channel(_cfg(), "whatsapp") == ""  # type: ignore[arg-type]

    def test_email_priority_when_no_whatsapp(self):
        assert _resolve_cancel_otp_channel(_cfg(require_otp=True, require_phone_otp=True), "web") == "email"  # type: ignore[arg-type]

    def test_phone_priority_on_whatsapp_when_both(self):
        assert _resolve_cancel_otp_channel(_cfg(require_otp=True, require_phone_otp=True), "whatsapp") == "phone"  # type: ignore[arg-type]


class TestOtpChannelLabel:
    def test_phone_label_whatsapp(self):
        assert _otp_channel_label("phone", _cfg()) == "WhatsApp"  # type: ignore[arg-type]

    def test_email_label(self):
        assert _otp_channel_label("email", _cfg()) == "email"  # type: ignore[arg-type]