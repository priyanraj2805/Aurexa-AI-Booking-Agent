"""WhatsApp slots as plain text (no directive) - when user selects date, show all slots as messages."""
import uuid
from datetime import datetime, timedelta, timezone, time, date

import pytest
from httpx import AsyncClient
from sqlalchemy.future import select

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.booking import BusinessSchedule, ScheduleDay
from app.models.session import ChatSession
from app.models.whatsapp_connection import WhatsAppConnection
from app.core.config import settings

@pytest.mark.asyncio
async def test_whatsapp_date_shows_slots_as_plain_text(client: AsyncClient, db_session, monkeypatch):
    # Use service key for inbound
    monkeypatch.setattr(settings, "WA_GATEWAY_SERVICE_KEY", "test-gw-key")
    headers = {"X-Gateway-Key": "test-gw-key"}

    # Create org with config
    org = Organization(name="WA Slots Org", api_key=f"ds_test_{uuid.uuid4().hex[:16]}", is_active=True)
    db_session.add(org)
    await db_session.flush()
    config = OrgConfig(org_id=org.id, required_fields=["name", "email", "phone", "date", "time"], timezone="UTC", require_otp=False, require_phone_otp=False)
    db_session.add(config)
    # Business schedule for tomorrow + next 7 days to ensure horizon
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=2)).date()
    for i in range(7):
        d = tomorrow + timedelta(days=i)
        # Avoid duplicate day_of_week entries - only add for tomorrow's weekday, but ensure open all week for test
        # Check if already exists for this weekday
        existing = await db_session.execute(select(ScheduleDay).where(ScheduleDay.org_id == org.id, ScheduleDay.day_of_week == d.weekday()))
        if not existing.scalars().first():
            db_session.add(ScheduleDay(org_id=org.id, day_of_week=d.weekday(), is_active=True, open_time=time(9,0), close_time=time(17,0)))
    db_session.add(BusinessSchedule(org_id=org.id, slot_duration_minutes=60, booking_window_days=60, timezone="UTC"))
    await db_session.commit()
    await db_session.refresh(org)

    # WhatsApp connection
    row = WhatsAppConnection(org_id=org.id, status="CONNECTED", phone_number="919999999999", whatsapp_booking_enabled=True)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    # Pre-create a whatsapp session with name/email/phone already collected, missing only date/time
    # Then the next message with date should trigger slots display
    from app.models.session import ChatSession as CS
    pre_session = CS(
        org_id=org.id,
        channel="whatsapp",
        channel_user_id="919888888888",
        state_data={"name": "Test User", "email": "testuser@test.com", "phone": "9998887777"},
        missing_fields=["date", "time"],
        current_intent="PROVIDE_DATA",
        session_status="ACTIVE",
        otp_verified=True,
        phone_otp_verified=True,
    )
    db_session.add(pre_session)
    await db_session.commit()
    await db_session.refresh(pre_session)

    # Debug: check slot inventory directly before inbound
    from app.engine.slot_engine import compute_slot_inventory, apply_scheduling_policy_to_inventory, format_slot_time
    from app.engine.scheduling_policy import resolve_effective_timezone_policy
    _inv, _stat = await compute_slot_inventory(db_session, org.id, tomorrow, org_config=config)
    print(f"DEBUG inventory for {tomorrow}: len={len(_inv)} status={_stat}")
    for it in _inv[:3]:
        print(f"  slot {it}")
    _tz_res = resolve_effective_timezone_policy(requested_tz=None, org_tz="UTC", requested_source=None)
    _eff = str(_tz_res.get("effective_timezone") or "UTC")
    _inv2, _stat2, _ = apply_scheduling_policy_to_inventory(_inv, tomorrow, base_status=_stat, effective_timezone=_eff, allow_same_day_booking=False, slot_cutoff_minutes=5)
    print(f"DEBUG after policy: len={len(_inv2)} stat={_stat2}")
    for it in _inv2[:3]:
        print(f"  after {it}")

    # Now send date as whatsapp inbound - should get slots for that date as plain text
    payload = {
        "connectionId": str(row.id),
        "messageId": f"wamid.test_{uuid.uuid4().hex[:8]}",
        "from": "919888888888",
        "chatJid": "919888888888@s.whatsapp.net",
        "text": tomorrow.isoformat(),
    }
    r = await client.post("/api/v1/internal/whatsapp-qr/inbound", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body
    reply = body["reply"]
    # Should be text type, not interactive, and contain slots
    assert reply["type"] == "text", f"Expected text directive, got {reply}"
    text = reply["body"] or ""
    # Should contain Available slots and at least one time like 09:00
    assert "Available slots for" in text or "available" in text.lower(), f"No slots header in {text}"
    assert "09:00" in text or "9:00" in text or "•" in text, f"No slot times in {text}"
    # Should ask to reply with time
    assert "reply" in text.lower() or "preferred time" in text.lower(), f"No instruction in {text}"
    # Should NOT be interactive directive
    assert reply["type"] != "interactive_buttons" and reply["type"] != "interactive_list", "Should be plain text, not directive"
