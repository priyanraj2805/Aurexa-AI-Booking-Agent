"""Full check-appointment loop: ask -> collect -> DB lookup -> report.

Uses the aiosqlite test DB (no live Postgres needed).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.booking import TimeSlot, Booking
from app.models.session import ChatSession
from app.schemas.agent import ChatSessionState
from app.engine import check_booking_flow as flow


async def _make_org(db: AsyncSession) -> Organization:
    org = Organization(
        name="Lookup Org",
        api_key=f"ds_lk_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db.add(org)
    await db.flush()
    db.add(OrgConfig(
        org_id=org.id,
        timezone="UTC",
        required_fields=["name", "email", "phone", "date", "time"],
    ))
    await db.commit()
    await db.refresh(org)
    return org


async def _make_session(db: AsyncSession, org_id, **state) -> ChatSessionState:
    row = ChatSession(
        org_id=org_id,
        channel="web",
        state_data=dict(state.get("collected_data") or {}),
        missing_fields=list(state.get("missing_fields") or []),
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ChatSessionState(
        session_id=str(row.id),
        org_id=str(org_id),
        channel="web",
        collected_data=dict(row.state_data or {}),
        missing_fields=list(row.missing_fields or []),
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )


async def _make_booking(
    db: AsyncSession, org, session_id, email, phone, start_offset_hours
) -> Booking:
    start = datetime.now(timezone.utc) + timedelta(hours=start_offset_hours)
    slot = TimeSlot(
        org_id=org.id, start_time=start, end_time=start + timedelta(hours=1)
    )
    db.add(slot)
    await db.flush()
    booking = Booking(
        org_id=org.id,
        session_id=session_id,
        slot_id=slot.id,
        customer_name="Look Uper",
        customer_email=email,
        customer_phone=phone,
    )
    db.add(booking)
    await db.commit()
    return booking


async def _org_config(db: AsyncSession, org) -> OrgConfig:
    from sqlalchemy import select

    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    return res.scalars().first()


@pytest.mark.asyncio
async def test_no_contact_asks_and_marks_pending(db_session: AsyncSession):
    org = await _make_org(db_session)
    sess = await _make_session(db_session, org.id)
    action = await flow.handle_check_booking(
        db_session, org, await _org_config(db_session, org), sess
    )
    assert "email or phone" in action.message
    assert flow.is_check_pending(sess) is True
    # Pending survives a persist round-trip.
    assert sess.collected_data.get(flow.CHECK_PENDING_KEY) is True


@pytest.mark.asyncio
async def test_contact_completes_lookup_and_reports_upcoming(
    db_session: AsyncSession,
):
    org = await _make_org(db_session)
    sess = await _make_session(db_session, org.id)
    cfg = await _org_config(db_session, org)
    await flow.handle_check_booking(db_session, org, cfg, sess)
    assert flow.is_check_pending(sess) is True

    await _make_booking(
        db_session, org, sess.session_id,
        "finder@example.com", "+15550001111", start_offset_hours=48,
    )
    action = await flow.maybe_complete_pending_lookup(
        db_session, org, cfg, sess, "my email is finder@example.com"
    )
    assert action is not None
    assert "upcoming appointment" in action.message
    assert "finder@example.com" not in action.message  # identity not leaked back
    assert flow.is_check_pending(sess) is False
    # Contact persisted for the booking flow; email drops from missing.
    sess.missing_fields = ["email", "date"]
    await flow.handle_check_booking(
        db_session, org, cfg, sess,
        contact_override=("email", "finder@example.com"),
    )
    assert "email" not in (sess.missing_fields or [])


@pytest.mark.asyncio
async def test_no_bookings_reports_none(db_session: AsyncSession):
    org = await _make_org(db_session)
    sess = await _make_session(
        db_session, org.id, collected_data={"email": "nobody@example.com"}
    )
    action = await flow.handle_check_booking(
        db_session, org, await _org_config(db_session, org), sess
    )
    assert "don't have any previous bookings" in action.message


@pytest.mark.asyncio
async def test_past_bookings_listed_separately(db_session: AsyncSession):
    org = await _make_org(db_session)
    sess = await _make_session(
        db_session, org.id, collected_data={"email": "both@example.com"}
    )
    cfg = await _org_config(db_session, org)
    await _make_booking(
        db_session, org, sess.session_id,
        "both@example.com", "+15550002222", start_offset_hours=-72,
    )
    await _make_booking(
        db_session, org, sess.session_id,
        "both@example.com", "+15550002222", start_offset_hours=72,
    )
    action = await flow.handle_check_booking(db_session, org, cfg, sess)
    assert "upcoming appointment" in action.message
    assert "past appointment" in action.message.lower()


@pytest.mark.asyncio
async def test_pending_ignores_non_contact_message(db_session: AsyncSession):
    org = await _make_org(db_session)
    sess = await _make_session(db_session, org.id)
    cfg = await _org_config(db_session, org)
    await flow.handle_check_booking(db_session, org, cfg, sess)
    assert (
        await flow.maybe_complete_pending_lookup(
            db_session, org, cfg, sess, "never mind, book a new one"
        )
    ) is None
    assert flow.is_check_pending(sess) is True
    sess2 = await _make_session(db_session, org.id)
    assert (
        await flow.maybe_complete_pending_lookup(
            db_session, org, cfg, sess2, "test@example.com"
        )
    ) is None
