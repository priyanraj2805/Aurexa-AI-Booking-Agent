import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.booking import Booking, TimeSlot
from app.models.organization import Organization
from app.models.session import ChatSession


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


async def _make_org_and_slot(db_session):
    org = Organization(
        name="Legacy Compat Org",
        api_key=f"ds_legacy_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()

    slot = TimeSlot(
        org_id=org.id,
        start_time=datetime.fromisoformat("2026-01-15T09:00:00+00:00"),
        end_time=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
        is_booked=False,
    )
    db_session.add(slot)
    await db_session.commit()
    await db_session.refresh(org)
    await db_session.refresh(slot)
    return org, slot


@pytest.mark.asyncio
async def test_legacy_fixture_rows_load_via_current_models(db_session):
    legacy_session = _load_fixture("legacy_chat_session_row.json")
    legacy_booking = _load_fixture("legacy_booking_row.json")

    org, slot = await _make_org_and_slot(db_session)

    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()

    db_session.add(
        ChatSession(
            id=session_id,
            org_id=org.id,
            channel=legacy_session["channel"],
            channel_user_id=legacy_session.get("channel_user_id"),
            state_data=legacy_session["state_data"],
            missing_fields=legacy_session["missing_fields"],
            current_intent=legacy_session["current_intent"],
            session_status=legacy_session["session_status"],
            otp_verified=legacy_session["otp_verified"],
            pending_cancel=legacy_session["pending_cancel"],
            is_active=legacy_session["is_active"],
        )
    )
    db_session.add(
        Booking(
            id=booking_id,
            org_id=org.id,
            session_id=session_id,
            slot_id=slot.id,
            customer_name=legacy_booking["customer_name"],
            customer_email=legacy_booking["customer_email"],
            customer_phone=legacy_booking["customer_phone"],
        )
    )
    await db_session.commit()

    loaded_session = (
        (await db_session.execute(select(ChatSession).where(ChatSession.id == session_id)))
        .scalars()
        .one()
    )
    loaded_booking = (
        (await db_session.execute(select(Booking).where(Booking.id == booking_id)))
        .scalars()
        .one()
    )

    assert loaded_session.channel == legacy_session["channel"]
    assert loaded_session.session_status == legacy_session["session_status"]
    assert loaded_session.state_data["name"] == legacy_session["state_data"]["name"]

    assert loaded_booking.customer_name == legacy_booking["customer_name"]
    assert loaded_booking.customer_email == legacy_booking["customer_email"]
    assert loaded_booking.session_id == session_id


@pytest.mark.asyncio
async def test_legacy_fixture_rows_can_be_updated_and_committed(db_session):
    legacy_session = _load_fixture("legacy_chat_session_row.json")
    legacy_booking = _load_fixture("legacy_booking_row.json")

    org, slot = await _make_org_and_slot(db_session)
    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()

    db_session.add(
        ChatSession(
            id=session_id,
            org_id=org.id,
            channel=legacy_session["channel"],
            state_data=legacy_session["state_data"],
            missing_fields=legacy_session["missing_fields"],
            current_intent=legacy_session["current_intent"],
            session_status=legacy_session["session_status"],
            otp_verified=legacy_session["otp_verified"],
            pending_cancel=legacy_session["pending_cancel"],
            is_active=legacy_session["is_active"],
        )
    )
    db_session.add(
        Booking(
            id=booking_id,
            org_id=org.id,
            session_id=session_id,
            slot_id=slot.id,
            customer_name=legacy_booking["customer_name"],
            customer_email=legacy_booking["customer_email"],
            customer_phone=legacy_booking["customer_phone"],
        )
    )
    await db_session.commit()

    session_row = (
        (await db_session.execute(select(ChatSession).where(ChatSession.id == session_id)))
        .scalars()
        .one()
    )
    booking_row = (
        (await db_session.execute(select(Booking).where(Booking.id == booking_id)))
        .scalars()
        .one()
    )

    updated_state_data = dict(session_row.state_data)
    updated_state_data["notes"] = "legacy row updated"
    session_row.state_data = updated_state_data
    session_row.current_intent = "confirm_booking"
    booking_row.customer_phone = "+1-555-0145"
    booking_row.user_timezone = "Asia/Kolkata"
    booking_row.timezone_source = "user_selected"
    await db_session.commit()

    await db_session.refresh(session_row)
    await db_session.refresh(booking_row)

    assert session_row.state_data["notes"] == "legacy row updated"
    assert session_row.current_intent == "confirm_booking"
    assert booking_row.customer_phone == "+1-555-0145"
    assert booking_row.user_timezone == "Asia/Kolkata"
    assert booking_row.timezone_source == "user_selected"


@pytest.mark.asyncio
async def test_legacy_roundtrip_preserves_required_booking_session_fields(db_session):
    legacy_session = _load_fixture("legacy_chat_session_row.json")
    legacy_booking = _load_fixture("legacy_booking_row.json")

    org, slot = await _make_org_and_slot(db_session)
    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()

    db_session.add(
        ChatSession(
            id=session_id,
            org_id=org.id,
            channel=legacy_session["channel"],
            state_data=legacy_session["state_data"],
            missing_fields=legacy_session["missing_fields"],
            current_intent=legacy_session["current_intent"],
            session_status=legacy_session["session_status"],
            otp_verified=legacy_session["otp_verified"],
            pending_cancel=legacy_session["pending_cancel"],
            is_active=legacy_session["is_active"],
        )
    )
    db_session.add(
        Booking(
            id=booking_id,
            org_id=org.id,
            session_id=session_id,
            slot_id=slot.id,
            customer_name=legacy_booking["customer_name"],
            customer_email=legacy_booking["customer_email"],
            customer_phone=legacy_booking["customer_phone"],
        )
    )
    await db_session.commit()

    await db_session.execute(select(ChatSession).where(ChatSession.id == session_id))
    await db_session.execute(select(Booking).where(Booking.id == booking_id))

    final_session = (
        (await db_session.execute(select(ChatSession).where(ChatSession.id == session_id)))
        .scalars()
        .one()
    )
    final_booking = (
        (await db_session.execute(select(Booking).where(Booking.id == booking_id)))
        .scalars()
        .one()
    )

    assert final_session.channel == legacy_session["channel"]
    assert final_session.session_status == legacy_session["session_status"]
    assert final_session.state_data["email"] == legacy_session["state_data"]["email"]
    assert final_booking.customer_name == legacy_booking["customer_name"]
    assert final_booking.customer_email == legacy_booking["customer_email"]
    assert final_booking.session_id == session_id
    assert final_booking.slot_id == slot.id
