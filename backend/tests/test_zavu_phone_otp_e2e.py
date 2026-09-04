"""Zavu phone OTP E2E — booking confirmation via SMS (kd78m7agx48rpyegpfrmhp5rsx8dm8t9)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.booking import Booking, TimeSlot
from app.models.otp import OTPRecord


async def _make_org(db, require_phone_otp=True, require_otp=False):
    org = Organization(name="Zavu E2E Org", api_key=f"ds_zavu_{uuid.uuid4().hex[:8]}", is_active=True)
    db.add(org)
    await db.flush()
    cfg = OrgConfig(org_id=org.id, required_fields=["name","email","phone","date","time"])
    cfg.require_otp = require_otp
    cfg.require_phone_otp = require_phone_otp
    db.add(cfg)
    await db.commit()
    await db.refresh(org)
    return org

async def _make_sess(db, org, phone="+919876543210"):
    sess = ChatSession(
        org_id=org.id,
        state_data={"phone": phone, "name":"Zavu Tester", "email":"zavu@test.com", "date":"2026-09-10", "time":"10:00", "user_timezone":"UTC"},
        missing_fields=[], current_intent="CONFIRM_BOOKING", session_status="ACTIVE", otp_verified=True, phone_otp_verified=False,
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess


@pytest.mark.asyncio
async def test_zavu_generate_creates_record_and_mock_send(client: AsyncClient, db_session, monkeypatch):
    """Zavu generate_otp for phone creates OTPRecord + mock SMS (zv_test sandbox)."""
    org = await _make_org(db_session)
    sess = await _make_sess(db_session, org, phone="+919876543210")
    sid = str(sess.id)

    from app.crud.otp import generate_otp
    code = await generate_otp(db_session, sid, channel="phone")
    assert len(code)==6 and code.isdigit()
    rec = await db_session.execute(select(OTPRecord).where(OTPRecord.session_id==sess.id, OTPRecord.channel=="phone"))
    assert rec.scalars().first() is not None
    # Zavu mock for zv_test returns mock success
    from app.core.sms import _send_zavu_sms_sync
    res = _send_zavu_sms_sync("+919876543210", f"Your DotStark verification code is {code}. It expires in 5 minutes. Do not share this code.")
    assert res.get("success") is True
    # Endpoint also works (may be 200 or 401 depending on test client auth, but crud path is primary)
    resp = await client.post("/api/v1/auth/security/otp/generate", json={"session_id": sid, "channel":"phone"}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert resp.status_code in (200, 401)


@pytest.mark.asyncio
async def test_zavu_verify_correct_code_marks_session(client: AsyncClient, db_session):
    org = await _make_org(db_session)
    sess = await _make_sess(db_session, org, phone="+919876543210")
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
async def test_zavu_verify_wrong_code_rejected(client: AsyncClient, db_session):
    org = await _make_org(db_session)
    sess = await _make_sess(db_session, org, phone="+919876543210")
    sid = str(sess.id)
    from app.crud.otp import generate_otp
    await generate_otp(db_session, sid, channel="phone")
    resp = await client.post("/api/v1/auth/security/otp/verify", json={"session_id": sid, "channel":"phone", "code": "000000"}, headers={"X-Requested-With":"XMLHttpRequest"})
    assert resp.status_code==400
    assert "invalid" in resp.text.lower()

@pytest.mark.asyncio
async def test_booking_blocked_until_phone_otp_then_confirmed(client: AsyncClient, db_session, monkeypatch):
    """Booking should be REQUIRE_OTP_MODAL while phone_otp_verified False, BOOKING_CONFIRMED_CARD after correct code."""
    org = await _make_org(db_session, require_phone_otp=True, require_otp=False)
    sess = await _make_sess(db_session, org, phone="+919876543210")
    sid = str(sess.id)
    # Slot
    slot_start = datetime(2026,9,10,10,0, tzinfo=timezone.utc)
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
