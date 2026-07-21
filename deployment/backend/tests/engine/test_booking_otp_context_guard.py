from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine import message_processor
from app.schemas.agent import ChatSessionState


@pytest.mark.asyncio
async def test_handle_booking_flow_requires_fresh_otp_for_new_booking_context(monkeypatch):
    chat_session = ChatSessionState(
        session_id="session-otp-context",
        org_id="00000000-0000-0000-0000-000000000001",
        current_intent="CONFIRM_BOOKING",
        session_status="ACTIVE",
        otp_verified=True,
        missing_fields=[],
        collected_data={
            "email": "customer@example.com",
            "date": "2026-08-21",
            "time": "10:00",
            "_internal": {
                "otp_verified_key": "otp:session-otp-context:customer@example.com:2026-08-20:10:00",
                "otp_send_key": "otp:session-otp-context:customer@example.com:2026-08-20:10:00",
            },
        },
    )
    org = SimpleNamespace(id="org-1")

    monkeypatch.setattr(message_processor, "_lock_slot_for_booking", AsyncMock())
    monkeypatch.setattr("app.crud.otp.generate_otp", AsyncMock(return_value="123456"))
    monkeypatch.setattr(
        "app.crud.otp.get_otp_policy_metadata",
        AsyncMock(return_value={"resends_left": 3}),
    )
    monkeypatch.setattr("app.core.mail.send_otp_email", AsyncMock())
    monkeypatch.setattr("app.engine.message_processor.crud_chat.update_chat_session", AsyncMock())
    execute_booking = AsyncMock(return_value=("confirmed", "BOOKING_CONFIRMED_CARD"))
    monkeypatch.setattr(message_processor, "_execute_booking", execute_booking)

    message, directive, field_meta = await message_processor._handle_booking_flow(
        db=SimpleNamespace(),
        org=org,
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        intent="CONFIRM_BOOKING",
        require_otp=True,
        fd_map={},
        background_tasks=None,
    )

    assert directive == "REQUIRE_OTP_MODAL"
    assert "verification code" in message.lower()
    assert field_meta == {"resends_left": 3}
    assert chat_session.otp_verified is False
    assert execute_booking.await_count == 0
