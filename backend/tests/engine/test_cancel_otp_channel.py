from types import SimpleNamespace

import pytest

from app.engine.cancel_flow import (
    _lookup_action,
    _lookup_contact_kind,
    _lookup_contact_prompt,
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


class TestLookupAsksOnlyOtpChannel:
    """The lookup must ask ONLY for the contact the OTP will use.

    Production pain: phone-OTP orgs asked "email or phone number" while the
    code could only reach the phone — a wasted turn and a confused customer.
    """

    def test_phone_otp_whatsapp_asks_phone_only(self):
        cfg = _cfg(require_phone_otp=True)
        out = _lookup_contact_prompt(
            "Please enter the {contact} you used.", cfg, "whatsapp"
        )
        assert out == "Please enter the phone number you used."
        assert "email" not in out.lower()

    def test_email_only_asks_email_only(self):
        cfg = _cfg(require_otp=True)
        out = _lookup_contact_prompt(
            "Please enter the {contact} you used.", cfg, "whatsapp"
        )
        assert out == "Please enter the email address you used."
        assert "phone" not in out.lower()

    def test_unknown_config_falls_back_to_generic(self):
        out = _lookup_contact_prompt("Please enter the {contact} you used.", None, "")
        assert out == "Please enter the email or phone number you used."

    def test_lookup_action_meta_matches_channel(self):
        cfg = _cfg(require_phone_otp=True)
        action = _lookup_action("msg", org_config=cfg, channel="whatsapp")
        assert action.field_meta["label"] == "Phone Number"
        assert action.field_meta["placeholder"] == "+91 98765 43210"
        assert "email" not in action.field_meta["description"].lower()
        generic = _lookup_action("msg")
        assert generic.field_meta["label"] == "Email or Phone"


@pytest.mark.asyncio
async def test_cancel_otp_goes_to_booking_phone_despite_email_lookup(
    db_session, monkeypatch
):
    """Admin config wins over lookup input: phone-OTP org, booking found by
    EMAIL, but the code must still go to the booking's PHONE."""
    import uuid
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock

    from app.engine import cancel_flow
    from app.models.booking import Booking, TimeSlot
    from app.models.session import ChatSession
    from app.schemas.agent import ChatSessionState

    org_id = uuid.uuid4()
    start = datetime.now(timezone.utc) + timedelta(days=2)
    slot = TimeSlot(org_id=org_id, start_time=start, end_time=start + timedelta(hours=1))
    db_session.add(slot)
    await db_session.commit()
    await db_session.refresh(slot)
    booking = Booking(
        org_id=org_id,
        slot_id=slot.id,
        customer_name="Maya",
        customer_email="maya@test.com",
        customer_phone="+919814732658",
    )
    db_session.add(booking)
    await db_session.commit()
    row = ChatSession(
        org_id=org_id,
        channel="whatsapp",
        state_data={},
        missing_fields=[],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    state = ChatSessionState(
        session_id=str(row.id),
        org_id=str(org_id),
        channel="whatsapp",
        collected_data={},
        missing_fields=[],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )

    sent_to = {}

    async def fake_send_otp(self, phone, code, session_id):
        sent_to["phone"] = phone
        return {"success": True}

    async def fail_mail(*_a, **_k):
        raise AssertionError("email must not be used when phone-OTP is on")

    monkeypatch.setattr(
        "app.crud.otp.generate_otp", AsyncMock(return_value="123456")
    )
    monkeypatch.setattr(
        "app.core.otp_delivery.whatsapp_provider.WhatsAppProvider.send_otp",
        fake_send_otp,
    )
    monkeypatch.setattr("app.core.mail.send_otp_email", fail_mail)

    from types import SimpleNamespace

    org = SimpleNamespace(id=org_id)
    cfg = SimpleNamespace(require_otp=False, require_phone_otp=True)
    action = await cancel_flow._send_cancel_otp(
        db_session, org, cfg, state, booking, start, None
    )
    assert sent_to.get("phone") == "+919814732658"
    assert action.widget_directive == "REQUIRE_OTP_MODAL"