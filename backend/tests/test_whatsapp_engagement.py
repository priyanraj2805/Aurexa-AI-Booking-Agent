"""WhatsApp engagement: opt-out, language, window, lead, waitlist, funnel."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.session import ChatSession
from app.services import whatsapp_engagement as eng


async def _org(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="WA Org", api_key=f"ds_test_{uuid.uuid4().hex[:16]}", is_active=True
    )
    db_session.add(org)
    await db_session.flush()
    db_session.add(OrgConfig(org_id=org.id, required_fields=["name"]))
    await db_session.commit()
    await db_session.refresh(org)
    return org


# ── opt-out detection ───────────────────────────────────────────────
def test_stop_keywords_detected():
    assert eng.is_stop_request("STOP") is True
    assert eng.is_stop_request("stop") is True
    assert eng.is_stop_request("unsubscribe") is True
    assert eng.is_stop_request("Please stop") is True
    assert eng.is_stop_request("Stop!") is True
    assert eng.is_stop_request("opt out") is True


def test_normal_messages_are_not_stop():
    assert eng.is_stop_request("I want to book an appointment") is False
    assert eng.is_stop_request("stopover") is False
    assert eng.is_stop_request("") is False
    assert eng.is_stop_request("payment failed") is False


def test_start_request_detected():
    assert eng.is_start_request("START") is True
    assert eng.is_start_request("subscribe") is True
    assert eng.is_start_request("hello") is False


async def test_record_and_check_opt_out(db_session: AsyncSession):
    org = await _org(db_session)
    assert await eng.is_opted_out(db_session, org.id, "919876543210") is False
    created = await eng.record_opt_out(
        db_session, org.id, "919876543210", reason="stop"
    )
    assert created is True
    assert await eng.is_opted_out(db_session, org.id, "919876543210") is True
    # Second stop is idempotent (no duplicate row).
    again = await eng.record_opt_out(db_session, org.id, "919876543210")
    assert again is False
    # Other numbers unaffected.
    assert await eng.is_opted_out(db_session, org.id, "919999999999") is False


async def test_clear_opt_out_resubscribes(db_session: AsyncSession):
    org = await _org(db_session)
    await eng.record_opt_out(db_session, org.id, "919876543211")
    assert await eng.is_opted_out(db_session, org.id, "919876543211") is True
    assert await eng.clear_opt_out(db_session, org.id, "919876543211") is True
    assert await eng.is_opted_out(db_session, org.id, "919876543211") is False


# ── language detection ───────────────────────────────────────────────
def test_detect_language_scripts():
    assert eng.detect_language("Hello, book me a slot") == "en"
    assert eng.detect_language("मुझे कल appointment चाहिए") == "hi"
    assert eng.detect_language("مرحبا أريد حجز") == "ar"
    assert eng.detect_language("我要预约") == "zh"
    assert eng.detect_language("") == "en"


# ── 24h service window ──────────────────────────────────────────────
def test_within_service_window():
    now = datetime.now(timezone.utc)
    assert eng.within_service_window(now - timedelta(hours=2), now) is True
    assert eng.within_service_window(now - timedelta(hours=23), now) is True
    assert eng.within_service_window(now - timedelta(hours=25), now) is False
    # No inbound yet → outside (a business-initiated message must use a
    # template, not free text).
    assert eng.within_service_window(None, now) is False


# ── lead capture ────────────────────────────────────────────────────
async def test_capture_lead_is_idempotent_per_session(db_session: AsyncSession):
    org = await _org(db_session)
    sid = uuid.uuid4()
    lead1 = await eng.capture_lead(
        db_session, org.id, session_id=sid, phone="919876543210", name="A"
    )
    assert lead1 is not None
    lead2 = await eng.capture_lead(
        db_session, org.id, session_id=sid, phone="919876543210", name="B"
    )
    assert lead2 is not None
    assert lead2.id == lead1.id  # refreshed, not duplicated
    assert lead2.name == "B"
    rows = (
        await db_session.execute(
            select(eng.BookingLead).where(eng.BookingLead.session_id == sid)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_mark_lead_converted(db_session: AsyncSession):
    org = await _org(db_session)
    sid = uuid.uuid4()
    await eng.capture_lead(db_session, org.id, session_id=sid, phone="919876543210")
    await eng.mark_lead_converted(db_session, org.id, sid)
    row = (
        await db_session.execute(
            select(eng.BookingLead).where(eng.BookingLead.session_id == sid)
        )
    ).scalars().first()
    assert row.status == "converted"


# ── waitlist ────────────────────────────────────────────────────────
async def test_join_waitlist_is_idempotent(db_session: AsyncSession):
    org = await _org(db_session)
    sid = uuid.uuid4()
    w1 = await eng.join_waitlist(
        db_session, org.id, session_id=sid, phone="919876543210"
    )
    assert w1 is not None
    w2 = await eng.join_waitlist(
        db_session, org.id, session_id=sid, phone="919876543210"
    )
    assert w2 is not None and w2.id == w1.id
    entries = await eng.waiting_entries(db_session, org.id)
    assert len(entries) == 1


# ── funnel ──────────────────────────────────────────────────────────
async def test_funnel_breakdown(db_session: AsyncSession):
    org = await _org(db_session)
    sid = uuid.uuid4()
    await eng.track_step(db_session, org.id, "date", session_id=sid)
    await eng.track_step(db_session, org.id, "date", session_id=sid, outcome="completed")
    await eng.track_step(db_session, org.id, "time", session_id=sid)
    data = await eng.funnel_breakdown(db_session, org.id, days=30)
    assert data["steps"]["date"]["entered"] == 1
    assert data["steps"]["date"]["completed"] == 1
    assert data["steps"]["date"]["conversion"] == 1.0
    assert data["steps"]["time"]["entered"] == 1
    assert data["steps"]["time"]["conversion"] == 0.0


# ── delivery dedupe ─────────────────────────────────────────────────
async def test_already_delivered_blocks_second_send(db_session: AsyncSession):
    org = await _org(db_session)
    await eng.log_delivery(
        db_session, org.id, booking_id=None, channel="reminder_24h",
        phone="919876543210", status="sent",
    )
    # A second send for the same booking+channel is refused.
    assert await eng.already_delivered(db_session, None, "reminder_24h") is True
    # A different channel is independent.
    assert await eng.already_delivered(db_session, None, "reminder_1h") is False
