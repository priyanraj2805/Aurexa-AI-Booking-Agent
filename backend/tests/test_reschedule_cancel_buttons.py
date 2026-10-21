"""Confirmed-card Reschedule/Cancel buttons: deterministic routing.

Button phrases ("reschedule my booking" / "cancel my booking") must:
- reschedule: cancel own slot, keep name/email/phone, return date picker
  (never re-ask details), and persist identity across reloads;
- cancel: skip the contact lookup for the session's own booking and send
  the cancel OTP immediately (code -> cancelled).
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.booking import Booking, TimeSlot
from app.models.session import ChatSession


async def _org(db: AsyncSession) -> Organization:
    org = Organization(
        name="Button Flow Org",
        api_key=f"ds_test_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db.add(org)
    await db.flush()
    db.add(
        OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
        )
    )
    await db.commit()
    await db.refresh(org)
    return org


def _future_slot(day_offset: int, hour: int = 12):
    start = datetime.now(timezone.utc) + timedelta(days=day_offset)
    return start.replace(hour=hour, minute=0, second=0, microsecond=0)


async def _confirmed_session(db: AsyncSession, org_id, email="owner@test.com"):
    """CONFIRMED session owning an upcoming booking, details collected."""
    start = _future_slot(4)
    slot = TimeSlot(
        org_id=org_id, start_time=start,
        end_time=start + timedelta(hours=1), is_booked=True,
    )
    db.add(slot)
    await db.flush()
    session = ChatSession(
        org_id=org_id,
        state_data={
            "name": "Owner User",
            "email": email,
            "phone": "+911234567890",
            "date": start.date().isoformat(),
            "time": "12:00",
        },
        missing_fields=[],
        current_intent="CONFIRM_BOOKING",
        session_status="CONFIRMED",
        otp_verified=True,
    )
    db.add(session)
    await db.flush()
    booking = Booking(
        org_id=org_id,
        session_id=session.id,
        slot_id=slot.id,
        customer_name="Owner User",
        customer_email=email,
        customer_phone="+911234567890",
        user_slot_start_at=start,
        user_slot_end_at=start + timedelta(hours=1),
    )
    db.add(booking)
    await db.commit()
    await db.refresh(session)
    await db.refresh(booking)
    return session, booking


def test_reschedule_remove_keys_keep_identity():
    from app.engine.message_processor import RESCHEDULE_REMOVE_KEYS

    assert {"name", "email", "phone"} - set(RESCHEDULE_REMOVE_KEYS) == {
        "name",
        "email",
        "phone",
    }
    assert {"date", "time"} <= set(RESCHEDULE_REMOVE_KEYS)


async def test_find_own_upcoming_booking_skips_non_confirmed():
    from app.engine.cancel_flow import find_own_upcoming_booking

    sess = SimpleNamespace(session_status="ACTIVE", session_id=str(uuid.uuid4()), collected_data={})
    assert await find_own_upcoming_booking(None, None, sess) == (None, None)


async def test_reschedule_button_goes_straight_to_date_picker(
    client: AsyncClient, db_session: AsyncSession
):
    org = await _org(db_session)
    session, booking = await _confirmed_session(db_session, org.id)

    resp = await client.post(
        "/api/v1/chat/message/dispatch",
        json={
            "message": "reschedule my booking",
            "session_id": str(session.id),
            "mode": "booking",
        },
        headers={"X-Org-API-Key": org.api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"]["widget_directive"] == "REQUIRE_DATE_PICKER"

    # Old slot freed with the booking row.
    gone = await db_session.execute(select(Booking).where(Booking.id == booking.id))
    assert gone.scalars().first() is None

    # Identity survives on the persisted row (reload must not re-ask).
    await db_session.refresh(session)
    row = (
        (await db_session.execute(select(ChatSession).where(ChatSession.id == session.id)))
        .scalars()
        .first()
    )
    assert row.state_data.get("name") == "Owner User"
    assert row.state_data.get("email") == "owner@test.com"
    assert row.state_data.get("phone") == "+911234567890"
    assert "date" not in row.state_data
    assert row.missing_fields[:2] == ["date", "time"]


async def test_cancel_button_sends_otp_directly(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    async def _fake_otp_email(**kwargs):
        return {"success": True}

    monkeypatch.setattr("app.core.mail.send_otp_email", _fake_otp_email)

    org = await _org(db_session)
    session, booking = await _confirmed_session(db_session, org.id)

    resp = await client.post(
        "/api/v1/chat/message/dispatch",
        json={
            "message": "cancel my booking",
            "session_id": str(session.id),
            "mode": "booking",
        },
        headers={"X-Org-API-Key": org.api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    # No contact lookup: straight to the OTP modal.
    assert body["action"]["widget_directive"] == "REQUIRE_OTP_MODAL"

    # Booking untouched until the code verifies.
    alive = await db_session.execute(select(Booking).where(Booking.id == booking.id))
    assert alive.scalars().first() is not None

    # Cancel flow parked at OTP stage for this booking (refresh: the app
    # request runs on its own session).
    await db_session.refresh(session)
    row = (
        (await db_session.execute(select(ChatSession).where(ChatSession.id == session.id)))
        .scalars()
        .first()
    )
    flow = (row.state_data or {}).get("_cancel_flow") or {}
    assert flow.get("stage") == "otp"
    assert flow.get("booking_id") == str(booking.id)
