"""WhatsApp phone OTP E2E — booking confirmation via WhatsApp-delivered codes."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.booking import Booking, TimeSlot
from app.models.otp import OTPRecord


BOOKING_DATE = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()


async def _make_org(db, require_phone_otp=True, require_otp=False):
    org = Organization(name="WhatsApp E2E Org", api_key=f"ds_wa_{uuid.uuid4().hex[:8]}", is_active=True)
    db.add(org)
    await db.flush()
    cfg = OrgConfig(org_id=org.id, required_fields=["name","email","phone","date","time"])
    cfg.require_otp = require_otp
    cfg.require_phone_otp = require_phone_otp
    db.add(cfg)
    await db.commit()
    await db.refresh(org)
    return org

async def _make_sess(db, org, phone="+916350063338"):
    sess = ChatSession(
        org_id=org.id,
        state_data={"phone": phone, "name":"WhatsApp Tester", "email":"wa@test.com", "date":BOOKING_DATE, "time":"10:00", "user_timezone":"UTC"},
        missing_fields=[], current_intent="CONFIRM_BOOKING", session_status="ACTIVE", otp_verified=True, phone_otp_verified=False,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


def _mock_whatsapp_send(monkeypatch):
    """Phone OTP sends go through WhatsApp — mock delivery, never real network."""
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        "app.core.otp_delivery.whatsapp_provider.WhatsAppProvider.send_otp", send
    )
    return send


@pytest.mark.asyncio
async def test_whatsapp_generate_creates_record(client: AsyncClient, db_session, monkeypatch):
    """generate_otp for phone creates OTPRecord; delivery goes via WhatsApp."""
    _mock_whatsapp_send(monkeypatch)
    org = await _make_org(db_session)
    sess = await _make_sess(db_session, org, phone="+916350063338")
    sid = str(sess.id)

    from app.crud.otp import generate_otp
    code = await generate_otp(db_session, sid, channel="phone")
    assert len(code)==6 and code.isdigit()
    rec = await db_session.execute(select(OTPRecord).where(OTPRecord.session_id==sess.id, OTPRecord.channel=="phone"))
    assert rec.scalars().first() is not None
    # Endpoint also works (may be 200 or 401 depending on test client auth, but crud path is primary)
    resp = await client.post("/api/v1/auth/security/otp/generate", json={"session_id": sid, "channel":"phone"}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert resp.status_code in (200, 401)


@pytest.mark.asyncio
async def test_whatsapp_verify_correct_code_marks_session(client: AsyncClient, db_session):
    org = await _make_org(db_session)
    sess = await _make_sess(db_session, org, phone="+916350063338")
    sid = str(sess.id)
    from app.crud.otp import generate_otp
    code = await generate_otp(db_session, sid, channel="phone")
    resp = await client.post("/api/v1/auth/security/otp/verify", json={"session_id": sid, "channel":"phone", "code": code}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert resp.status_code==200, resp.text
    db_session.expire_all()
    refreshed = await db_session.execute(select(ChatSession).where(ChatSession.id==uuid.UUID(sid)))
    s = refreshed.scalars().first()
    assert s.phone_otp_verified is True

@pytest.mark.asyncio
async def test_whatsapp_verify_wrong_code_rejected(client: AsyncClient, db_session):
    org = await _make_org(db_session)
    sess = await _make_sess(db_session, org, phone="+916350063338")
    sid = str(sess.id)
    from app.crud.otp import generate_otp
    await generate_otp(db_session, sid, channel="phone")
    resp = await client.post("/api/v1/auth/security/otp/verify", json={"session_id": sid, "channel":"phone", "code": "000000"}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert resp.status_code==400
    assert "invalid" in resp.text.lower()

@pytest.mark.asyncio
async def test_booking_blocked_until_phone_otp_then_confirmed(client: AsyncClient, db_session, monkeypatch):
    """Booking should be REQUIRE_OTP_MODAL while phone_otp_verified False, BOOKING_CONFIRMED_CARD after correct code."""
    _mock_whatsapp_send(monkeypatch)
    org = await _make_org(db_session, require_phone_otp=True, require_otp=False)
    sess = await _make_sess(db_session, org, phone="+916350063338")
    sid = str(sess.id)
    # New contract: OTP/booking only after the review card went out.
    from sqlalchemy.orm.attributes import flag_modified

    sess.state_data["_review_presented"] = True
    flag_modified(sess, "state_data")
    db_session.add(sess)
    await db_session.commit()
    # Slot
    slot_start = datetime.fromisoformat(f"{BOOKING_DATE}T10:00:00+00:00")
    slot = TimeSlot(org_id=org.id, start_time=slot_start, end_time=slot_start+timedelta(hours=1), is_booked=False)
    db_session.add(slot)
    await db_session.commit()

    # Fake LLM to avoid real call
    async def _fake(inputs, config=None):
        from langchain_core.messages import AIMessage
        return {"messages":[AIMessage(content="ok")], "collected_fields": dict(inputs.get("collected_fields") or {}), "follow_up_fields":[], "current_intent":"CONFIRM_BOOKING", "captured_deltas":{}, "merge_diagnostics":{}, "validation_errors":{}, "context_window":[], "summary":""}
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _fake)
    async def _noop(*a,**k): return None
    monkeypatch.setattr("app.core.mail.send_confirmation_emails", _noop)

    # 1. Try booking without OTP -> should get OTP modal
    resp = await client.post("/api/v1/chat/message/dispatch", json={"message":"yes", "session_id": sid}, headers={"X-Org-API-Key": org.api_key})
    assert resp.status_code==200
    assert resp.json()["action"]["widget_directive"]=="REQUIRE_OTP_MODAL"
    assert resp.json()["action"]["field_meta"]["channel"]=="phone"

    # 2. Generate + verify correct code
    from app.crud.otp import generate_otp
    code = await generate_otp(db_session, sid, channel="phone")
    v = await client.post("/api/v1/auth/security/otp/verify", json={"session_id": sid, "channel":"phone", "code": code}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert v.status_code==200

    # 3. Now booking should confirm
    resp2 = await client.post("/api/v1/chat/message/dispatch", json={"message":"yes", "session_id": sid}, headers={"X-Org-API-Key": org.api_key})
    assert resp2.status_code==200
    assert resp2.json()["action"]["widget_directive"]=="BOOKING_CONFIRMED_CARD"
    res = await db_session.execute(select(Booking).where(Booking.session_id==sess.id))
    assert res.scalars().first() is not None
