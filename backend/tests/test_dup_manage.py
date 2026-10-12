"""Duplicate-booking manage flow: guard buttons + reschedule-by-id + cancel keyword."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.booking import TimeSlot, Booking
from app.engine.cancel_flow import is_cancel_intent


async def _org(db: AsyncSession) -> Organization:
    org = Organization(
        name="Dup Manage Org",
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


async def _slot(db: AsyncSession, org_id, start) -> TimeSlot:
    slot = TimeSlot(
        org_id=org_id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        is_booked=False,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return slot


def _future_slot(day_offset: int, hour: int = 12):
    start = datetime.now(timezone.utc) + timedelta(days=day_offset)
    return start.replace(hour=hour, minute=0, second=0, microsecond=0)


async def _booking(
    db: AsyncSession, org_id, session_id, email: str, start, name: str = "Dup User"
) -> Booking:
    slot = await _slot(db, org_id, start)
    booking = Booking(
        org_id=org_id,
        session_id=session_id,
        slot_id=slot.id,
        customer_name=name,
        customer_email=email,
        customer_phone="+911234567890",
        user_slot_start_at=start,
        user_slot_end_at=start + timedelta(hours=1),
    )
    slot.is_booked = True
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    return booking


def _session(
    db: AsyncSession,
    org_id,
    state: dict,
    missing: list,
    intent: str = "CONFIRM_BOOKING",
) -> ChatSession:
    session = ChatSession(
        org_id=org_id,
        state_data=state,
        missing_fields=missing,
        current_intent=intent,
        session_status="ACTIVE",
        otp_verified=True,
    )
    db.add(session)
    return session


def test_cancel_booking_keyword_matches_button_text():
    assert is_cancel_intent("Cancel booking") is True
    assert is_cancel_intent("cancel my booking") is True
    assert is_cancel_intent("Reschedule booking") is False


async def test_dup_guard_returns_reschedule_cancel_buttons(
    client: AsyncClient, db_session: AsyncSession
):
    """Confirm with an existing upcoming booking -> select card, id pinned."""
    org = await _org(db_session)
    cfg = (
        (await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org.id)))
        .scalars()
        .first()
    )
    cfg.require_otp = False
    await db_session.commit()

    old_sid = uuid.uuid4()
    await _booking(db_session, org.id, old_sid, "dup@test.com", _future_slot(4))

    new_sid = uuid.uuid4()
    await _slot(db_session, org.id, _future_slot(5))
    new_session = _session(
        db_session,
        org.id,
        {
            "name": "Dup User",
            "_review_presented": True,
            "email": "dup@test.com",
            "phone": "+911234567890",
            "date": _future_slot(5).date().isoformat(),
            "time": "12:00",
        },
        [],
    )
    db_session.add(new_session)
    await db_session.commit()
    await db_session.refresh(new_session)

    resp = await client.post(
        "/api/v1/chat/message/dispatch",
        json={"message": "yes please", "session_id": str(new_session.id)},
        headers={"X-Org-API-Key": org.api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"]["widget_directive"] == "REQUIRE_SELECT"
    options = body["action"]["field_meta"]["options"]
    assert "Reschedule booking" in options
    assert "Cancel booking" in options
    assert body["action"]["field_meta"]["duplicate_booking"] is True

    # No new booking may be created while the duplicate stands.
    rows = (
        (await db_session.execute(select(Booking).where(Booking.org_id == org.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_reschedule_dup_cancels_old_and_reopens_dates(
    client: AsyncClient, db_session: AsyncSession
):
    """Tapping 'Reschedule booking' cancels the old row and asks for a date."""
    org = await _org(db_session)

    old_sid = uuid.uuid4()
    old_booking = await _booking(
        db_session, org.id, old_sid, "rebook@test.com", _future_slot(4)
    )

    new_session = _session(
        db_session,
        org.id,
        {
            "name": "Rebook User",
            "email": "rebook@test.com",
            "phone": "+911234567890",
            "date": _future_slot(5).date().isoformat(),
            "time": "12:00",
            "_dup_booking_id": str(old_booking.id),
        },
        [],
        intent="PROVIDE_DATA",
    )
    db_session.add(new_session)
    await db_session.commit()
    await db_session.refresh(new_session)

    resp = await client.post(
        "/api/v1/chat/message/dispatch",
        json={"message": "Reschedule booking", "session_id": str(new_session.id)},
        headers={"X-Org-API-Key": org.api_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"]["widget_directive"] == "REQUIRE_DATE_PICKER"

    # Old booking row gone (slot freed with it).
    gone = await db_session.execute(select(Booking).where(Booking.id == old_booking.id))
    assert gone.scalars().first() is None

    # Session reset to date/time collection, contacts kept.
    await db_session.refresh(new_session)
    assert new_session.missing_fields == ["date", "time"]
