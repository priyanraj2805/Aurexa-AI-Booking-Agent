"""End-to-end proof that a verified OTP actually creates a Booking row.

The production symptom: the customer entered a valid code, was told "your
appointment is confirmed", and no Booking row was ever written — so the admin
calendar, the notification bell and the confirmation email all stayed empty
even though the reply claimed success.

This exercises the real booking path (_handle_booking_flow -> _execute_booking
-> attempt_booking) with a verified phone OTP and asserts a persisted booking.
If the chain breaks, this fails.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.future import select as _sel

from app.crud import chat as crud_chat
from app.engine import message_processor
from app.models.booking import Booking, TimeSlot
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.schemas.agent import parse_field_definitions

FULL = {
    "name": "William Kelvin",
    "email": "wk@example.com",
    "phone": "+916350063338",
    "date": (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),
    "time": "3:00 PM",
}


async def _org_with_slot(db_session):
    org = Organization(name=f"E2E {uuid.uuid4().hex[:6]}", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    cfg = OrgConfig(
        org_id=org.id,
        business_name="Dotstark",
        timezone="UTC",
        required_fields=["name", "email", "phone", "date", "time"],
        field_definitions=[
            {"name": n, "type": t, "label": n.title(), "required": True}
            for n, t in (
                ("name", "text"),
                ("email", "email"),
                ("phone", "phone"),
                ("date", "date"),
                ("time", "time"),
            )
        ],
        require_otp=False,
        require_phone_otp=True,   # admin switched phone OTP ON
        allow_same_day_booking=True,
        slot_cutoff_minutes=5,
    )
    db_session.add(cfg)
    await db_session.commit()

    day = datetime.now(timezone.utc) + timedelta(days=3)
    slot = TimeSlot(
        id=uuid.uuid4(),
        org_id=org.id,
        start_time=day.replace(hour=15, minute=0, second=0, microsecond=0),
        end_time=day.replace(hour=15, minute=30, second=0, microsecond=0),
        is_booked=False,
    )
    db_session.add(slot)
    await db_session.commit()
    return org, cfg, slot


async def _verified_session(db_session, org, slot):
    """A session with every field collected and the phone OTP verified."""
    created = await crud_chat.create_chat_session(
        db_session, str(org.id), channel="whatsapp",
        channel_user_id=f"wa_{uuid.uuid4().hex[:10]}",
    )
    sid = str(created.id)
    sess = await crud_chat.get_chat_session(db_session, sid)
    collected = dict(FULL)
    collected["locked_slot_id"] = str(slot.id)
    # New contract: OTP/booking only after the review card went out.
    collected["_review_presented"] = True
    # Verification context key, required by the booking flow's guard.
    collected["_internal"] = {
        "phone_otp_send_key": f"otp_phone:{sid}:{FULL['phone']}:{FULL['date']}:{FULL['time']}",
        "phone_otp_verified_key": f"otp_phone:{sid}:{FULL['phone']}:{FULL['date']}:{FULL['time']}",
    }
    sess.collected_data = collected
    sess.missing_fields = []
    sess.current_intent = "CONFIRM_BOOKING"
    sess.session_status = "ACTIVE"
    sess.phone_otp_verified = True
    await crud_chat.update_chat_session(db_session, sid, sess)
    return await crud_chat.get_chat_session(db_session, sid)


def _fd_map():
    return {
        fd.name: fd
        for fd in parse_field_definitions(["name", "email", "phone", "date", "time"])
    }


@pytest.mark.asyncio
async def test_verified_otp_creates_a_booking_row(db_session, monkeypatch):
    """The whole point: verified code -> persisted Booking + CONFIRMED."""
    org, cfg, slot = await _org_with_slot(db_session)
    sess = await _verified_session(db_session, org, slot)

    async def _fake_confirm_email(*_a, **_k):
        return None

    monkeypatch.setattr("app.core.mail.send_confirmation_emails", _fake_confirm_email)
    monkeypatch.setattr(
        "app.engine.message_processor.log_audit_event", lambda *a, **k: None
    )

    msg, directive, _meta = await message_processor._handle_booking_flow(
        db_session,
        org,
        cfg,
        sess,
        "CONFIRM_BOOKING",
        False,               # require_otp off (admin chose phone OTP)
        _fd_map(),
        None,
        dispatch_notifications=False,
        require_phone_otp=True,
    )

    rows = (
        await db_session.execute(_sel(Booking).where(Booking.org_id == org.id))
    ).scalars().all()

    assert len(rows) == 1, (
        "no Booking row was created - this is the production bug. "
        f"reply={msg!r} directive={directive!r}"
    )
    booking = rows[0]
    assert booking.customer_email == FULL["email"]
    assert booking.customer_phone == FULL["phone"]
    assert str(directive) == "BOOKING_CONFIRMED_CARD"

    final = await crud_chat.get_chat_session(db_session, str(sess.session_id))
    assert final.session_status == "CONFIRMED"
    assert "confirm" in msg.lower()


@pytest.mark.asyncio
async def test_booking_is_not_created_while_otp_still_unverified(db_session, monkeypatch):
    """Unverified OTP must NOT book - fail closed."""
    org, cfg, slot = await _org_with_slot(db_session)
    sess = await _verified_session(db_session, org, slot)
    sess.phone_otp_verified = False
    sess.collected_data = {k: v for k, v in sess.collected_data.items()
                           if k != "_internal"}
    await crud_chat.update_chat_session(db_session, str(sess.session_id), sess)
    sess = await crud_chat.get_chat_session(db_session, str(sess.session_id))

    generated = {}

    async def _fake_gen_otp(*_a, **_k):
        generated["called"] = True
        return "123456"

    async def _fake_send(*_a, **_k):
        return {"success": True, "message_id": "wamid.TEST"}

    monkeypatch.setattr("app.crud.otp.generate_otp", _fake_gen_otp)
    monkeypatch.setattr(
        "app.core.otp_delivery.whatsapp_provider.WhatsAppProvider.send_otp",
        _fake_send,
    )

    msg, directive, _meta = await message_processor._handle_booking_flow(
        db_session, org, cfg, sess, "CONFIRM_BOOKING", False, _fd_map(), None,
        dispatch_notifications=False, require_phone_otp=True,
    )

    rows = (
        await db_session.execute(_sel(Booking).where(Booking.org_id == org.id))
    ).scalars().all()
    assert rows == [], "a booking was created without OTP verification"
    assert directive == "REQUIRE_OTP_MODAL"
