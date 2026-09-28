"""8-hour WhatsApp inactivity expiry — 11 required tests.

Covers the WhatsApp Booking Agent 8h window:
- inactivity-based, not 24h API window
- last_activity_at sliding window
- expiry BEFORE history load
- session-scoped history/directives/OTP
- timezone-aware, boundary <=8h vs >8h
- race-safe simultaneous creation
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.future import select

from app.crud import chat as crud_chat
from app.models.session import ChatSession
from app.models.booking_directive import BookingDirective
from app.models.message import ChatMessage
from app.models.config import OrgConfig
from app.models.organization import Organization


WHATSAPP_TTL = crud_chat.WHATSAPP_SESSION_TTL  # 8h

async def _make_org(db):
    org = Organization(name="ExOrg", api_key=f"ds_{uuid.uuid4().hex[:16]}", is_active=True)
    db.add(org)
    await db.flush()
    cfg = OrgConfig(org_id=org.id, required_fields=["name", "email", "phone", "date", "time"])
    db.add(cfg)
    await db.commit()
    await db.refresh(org)
    return org

def _now():
    return datetime.now(timezone.utc)

# TEST 1
@pytest.mark.asyncio
async def test_first_message_creates_new_session(db_session):
    org = await _make_org(db_session)
    sess = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000001")
    assert sess.session_id
    # should be ACTIVE
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(sess.session_id)))).scalars().first()
    assert row.session_status in ("ACTIVE", "CONFIRMING", "active", "human_active")
    assert row.is_active is True
    assert row.last_activity_at is not None

# TEST 2
@pytest.mark.asyncio
async def test_second_message_within_30m_same_session(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000002")
    # simulate 30m later
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    row.last_activity_at = _now() - timedelta(minutes=30)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000002")
    assert s1.session_id == s2.session_id

# TEST 3
@pytest.mark.asyncio
async def test_message_after_7h59m_same_session(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000003")
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    row.last_activity_at = _now() - timedelta(hours=7, minutes=59)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000003")
    assert s1.session_id == s2.session_id

# TEST 4
@pytest.mark.asyncio
async def test_message_after_8h1m_new_session(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000004")
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    row.last_activity_at = _now() - timedelta(hours=8, minutes=1)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000004")
    assert s1.session_id != s2.session_id
    # old expired
    old = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    assert old.session_status == "EXPIRED"
    assert old.is_active is False
    # new active
    new = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s2.session_id)))).scalars().first()
    assert new.session_status in ("ACTIVE", "CONFIRMING")
    assert new.is_active is True
    # new starts clean
    assert new.otp_verified is False
    assert new.phone_otp_verified is False
    assert new.missing_fields is not None

# TEST 5
@pytest.mark.asyncio
async def test_old_otp_required_not_carried_after_expiry(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000005")
    # simulate OTP_REQUIRED state
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    row.missing_fields = []
    row.current_intent = "CONFIRM_BOOKING"
    row.state_data = {"phone": "+919999000005", "active_directive_id": str(uuid.uuid4()), "_otp_phone_policy": {"last_sent_at": _now().isoformat()}}
    row.last_activity_at = _now() - timedelta(hours=9)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000005")
    assert s1.session_id != s2.session_id
    # new session must NOT have OTP state
    assert s2.otp_verified is False
    assert s2.phone_otp_verified is False
    # collected_data should not contain old phone's OTP pending? It starts fresh snapshot only
    assert "_otp_phone_policy" not in (s2.collected_data or {})
    assert "active_directive_id" not in (s2.collected_data or {})

# TEST 6
@pytest.mark.asyncio
async def test_old_select_date_directive_not_carried(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000006")
    # create a pending directive for old session
    old_id = uuid.UUID(s1.session_id)
    d = BookingDirective(
        org_id=org.id,
        customer_id="919999000006",
        session_id=old_id,
        type="SELECT_DATE",
        status="PENDING",
        title="Select date",
        description="pick",
        options=[{"id": "d1", "label": "2026-10-10", "value": "2026-10-10"}],
    )
    db_session.add(d)
    await db_session.commit()
    # expire
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == old_id))).scalars().first()
    row.last_activity_at = _now() - timedelta(hours=9)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000006")
    assert s1.session_id != s2.session_id
    # old directive should be EXPIRED
    await db_session.refresh(d)
    # re-fetch
    d2 = (await db_session.execute(select(BookingDirective).where(BookingDirective.id == d.id))).scalars().first()
    assert d2.status == "EXPIRED"
    # new session has no active_directive_id
    assert "active_directive_id" not in (s2.collected_data or {})

# TEST 7
@pytest.mark.asyncio
async def test_old_select_slot_not_carried(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000007")
    old_id = uuid.UUID(s1.session_id)
    d = BookingDirective(
        org_id=org.id,
        customer_id="919999000007",
        session_id=old_id,
        type="SELECT_SLOT",
        status="PENDING",
        title="Select slot",
        options=[{"id": "s1", "label": "10:00 AM", "value": "10:00 AM"}],
    )
    db_session.add(d)
    await db_session.commit()
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == old_id))).scalars().first()
    row.last_activity_at = _now() - timedelta(hours=9)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000007")
    assert s1.session_id != s2.session_id
    d2 = (await db_session.execute(select(BookingDirective).where(BookingDirective.id == d.id))).scalars().first()
    assert d2.status == "EXPIRED"

# TEST 8
@pytest.mark.asyncio
async def test_old_history_not_sent_to_llm_after_expiry(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000008")
    await crud_chat.save_message(db_session, s1.session_id, "user", "I want to book")
    await crud_chat.save_message(db_session, s1.session_id, "agent", "Sure, name?")
    # expire
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    row.last_activity_at = _now() - timedelta(hours=9)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000008")
    # new session history must be empty
    msgs_new = await crud_chat.get_session_messages(db_session, s2.session_id)
    assert len(msgs_new) == 0
    # old history still exists but session-scoped
    msgs_old = await crud_chat.get_session_messages(db_session, s1.session_id)
    assert len(msgs_old) == 2
    # get_latest_channel_checkpoint must not return old
    chk = await crud_chat.get_latest_channel_checkpoint(db_session, str(org.id), "whatsapp", "919999000008")
    assert chk is None  # new session has no checkpoint yet

# TEST 9
@pytest.mark.asyncio
async def test_redis_state_not_affect_new_conversation(db_session):
    # Debouncer uses per-session asyncio.Lock keyed by session_id, not phone.
    # After expiry, new session_id gets a fresh lock, old lock does not leak.
    from app.core.whatsapp_debouncer import get_session_lock
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000009")
    lock1 = get_session_lock(s1.session_id)
    s1_row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    s1_row.last_activity_at = _now() - timedelta(hours=9)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000009")
    lock2 = get_session_lock(s2.session_id)
    assert s1.session_id != s2.session_id
    assert lock1 is not lock2

# TEST 10
@pytest.mark.asyncio
async def test_simultaneous_messages_after_expiry_no_duplicate_sessions(db_session):
    import asyncio
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000010")
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    row.last_activity_at = _now() - timedelta(hours=9)
    await db_session.commit()
    # two concurrent get_or_create
    c1, c2 = await asyncio.gather(
        crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000010"),
        crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000010"),
    )
    # Both should return same new session (no duplicate active)
    assert c1.session_id == c2.session_id
    # Only one new active session exists
    active = (
        await db_session.execute(
            select(ChatSession).where(
                ChatSession.org_id == org.id,
                ChatSession.channel == "whatsapp",
                ChatSession.channel_user_id == "919999000010",
                ChatSession.is_active.is_(True),
            )
        )
    ).scalars().all()
    assert len(active) == 1

# TEST 11
@pytest.mark.asyncio
async def test_boundary_8h_exact(db_session):
    org = await _make_org(db_session)
    s1 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000011")
    row = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s1.session_id)))).scalars().first()
    # Exactly 8h - 5s -> should continue (<=8h) — use 8h-5s to avoid clock drift between _now() and internal now
    row.last_activity_at = _now() - timedelta(hours=8) + timedelta(seconds=5)
    await db_session.commit()
    s2 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000011")
    assert s1.session_id == s2.session_id
    # 8h + 1s -> new
    row2 = (await db_session.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(s2.session_id)))).scalars().first()
    row2.last_activity_at = _now() - timedelta(hours=8, seconds=1)
    await db_session.commit()
    s3 = await crud_chat.get_or_create_channel_session(db_session, str(org.id), "whatsapp", "919999000011")
    assert s3.session_id != s2.session_id
    # Also verify <=8h boundary via helper directly
    assert crud_chat._is_session_expired(_now() - timedelta(hours=8), _now()) is False
    assert crud_chat._is_session_expired(_now() - timedelta(hours=8, seconds=1), _now()) is True
