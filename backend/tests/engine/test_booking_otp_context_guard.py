from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine import message_processor
from app.engine.agent import graph as booking_graph
from app.engine.user_visible_text import looks_like_confirmation_claim
from app.schemas.agent import ChatSessionState


parametrize = pytest.mark.parametrize


@parametrize(
    "message, expected",
    [
        ("Your appointment is booked! 🎉", True),
        ("Your booking is confirmed!", True),
        ("Your slot is booked.", True),
        ("successfully booked", True),
        ("Appointment confirmed!", True),
        ("Your appointment has been booked", True),
        ("Booking confirmed", True),
        ("Appointment is booked", True),
        ("Your appointment is now successfully booked", True),
        ("Great news - your appointment has been booked", True),
        ("is your slot booked?", False),
        ("your slot is not booked", False),
        ("no booking confirmed", False),
        ("was your appointment confirmed?", False),
        ("I cannot confirm that booking", False),
        ("Do you want to book?", False),
        ("please confirm the booking", False),
        ("That slot is not available yet", False),
    ],
)
def test_looks_like_confirmation_claim(message: str, expected: bool):
    assert looks_like_confirmation_claim(message) is expected


@parametrize(
    "message, expected",
    [
        ("10:00 AM", True),
        ("10:00", True),
        ("10:00am", True),
        ("7:30 pm", True),
        ("August 7th 2026", True),
        ("2026-08-07", True),
        ("08/07/2026", True),
        ("07-08-2026", True),
        ("5550100999", True),
        ("+1 555-010-0999", True),
        ("qa.tester@test.com", True),
        ("virtual|9818c411-2d81-4dbe-abff-81baafbb8d41|2026-08-07T10:00:00+00:00", True),
        ("Yes", True),
        ("yes, please confirm", True),
        ("go ahead and book it", True),
        ("Sure", True),
        ("I want to book an appointment", True),
        ("My name is Priyanshu", True),
        ("I'd like to cancel my appointment", True),
        ("What are your business hours?", False),
        ("Do you offer weekend appointments?", False),
        ("Tell me about your services", False),
        ("what is your cancellation policy", False),
        ("How much does it cost?", False),
        ("", True),
    ],
)
def test_should_skip_rag(message: str, expected: bool):
    assert booking_graph._should_skip_rag(message) is expected


@parametrize(
    "message, expected",
    [
        ("10:00 AM", True),
        ("2026-08-07", True),
        ("5550100999", True),
        ("qa.tester@test.com", True),
        ("virtual|9818c411-2d81-4dbe-abff-81baafbb8d41|2026-08-07T10:00:00+00:00", True),
        ("Yes", True),
        ("yes, please confirm", True),
        ("go ahead and book it", True),
        ("August 7th 2026", False),
        ("I want to book an appointment", False),
        ("What are your business hours?", False),
        ("My name is Priyanshu", False),
    ],
)
def test_looks_like_booking_field_value(message: str, expected: bool):
    assert booking_graph._looks_like_booking_field_value(message) is expected


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
