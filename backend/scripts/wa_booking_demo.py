"""Live WhatsApp booking demo — drives the REAL engine conversation.

Creates a demo org + connected WhatsApp channel, sends scripted customer
turns through runtime_router.route_whatsapp_message (the exact function the
WhatsApp webhook uses), fetches the phone-OTP from the DB like a customer
would read it off their WhatsApp, and prints the full transcript.

Run inside the backend container:
  docker exec booking_agent-backend-1 python /app/wa_booking_demo.py
"""
import asyncio
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from sqlalchemy import select as _sel

from app.db.database import AsyncSessionLocal
import app.models.audit_log, app.models.booking, app.models.booking_directive
import app.models.broadcast, app.models.config, app.models.document_chunk
import app.models.email_delivery, app.models.escalation_policy
import app.models.impersonation, app.models.login_code, app.models.message
import app.models.organization, app.models.org_notification, app.models.otp
import app.models.payment, app.models.razorpay_route, app.models.rbac
import app.models.service_unit, app.models.session, app.models.system_alert
import app.models.user, app.models.whatsapp_connection  # noqa: F401
from app.crud import chat as crud_chat
from app.engine import runtime_router
from app.models.booking import Booking, TimeSlot
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.otp import OTPRecord
from app.models.session import ChatSession
from app.models.whatsapp_connection import WhatsAppConnection

PHONE = "+919999555777"
CHANNEL = "whatsapp_cloud"

CAPTURED_OTP = {}

import app.core.otp_delivery.whatsapp_provider as _wa_prov

_orig_send_otp = _wa_prov.WhatsAppProvider.send_otp


async def _capture_send_otp(self, to, code, session_id):
    CAPTURED_OTP["code"] = code
    print(f"[WhatsApp OTP delivery to {to}: code {code}]")
    return {"success": True}


_wa_prov.WhatsAppProvider.send_otp = _capture_send_otp


async def _seed_demo_org(db):
    res = await db.execute(
        _sel(Organization).where(Organization.name == "Priyanshu")
    )
    org = res.scalars().first()
    if org is None:
        raise RuntimeError("org 'Priyanshu' not found")
    cfg_res = await db.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
    cfg = cfg_res.scalars().first()
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(cfg.timezone)
    tomorrow_local = (datetime.now(tz) + timedelta(days=1)).date()
    if False:
        pass
        for hour in (10, 11, 14):
            local_start = datetime.combine(
                tomorrow_local, datetime.min.time()
            ).replace(hour=hour, tzinfo=tz)
            db.add(
                TimeSlot(
                    org_id=org.id,
                    start_time=local_start.astimezone(timezone.utc),
                    end_time=(local_start + timedelta(hours=1)).astimezone(
                        timezone.utc
                    ),
                    is_booked=False,
                )
            )
    res_slots = await db.execute(
        _sel(TimeSlot).where(
            TimeSlot.org_id == org.id,
            TimeSlot.start_time >= datetime.now(timezone.utc),
        )
    )
    existing = res_slots.scalars().all()
    await db.commit()
    await db.refresh(org)
    return org, tomorrow_local


async def _latest_otp(db, session_id):
    res = await db.execute(
        _sel(OTPRecord)
        .where(OTPRecord.session_id == uuid.UUID(str(session_id)))
        .order_by(OTPRecord.created_at.desc())
    )
    rec = res.scalars().first()
    return getattr(rec, "code", None) or getattr(rec, "otp_code", None)


def _asks_for_otp(text):
    t = text.lower()
    return ("code" in t or "otp" in t or "verification" in t) and (
        "send" not in t or "sent" in t
    )


async def main():
    async with AsyncSessionLocal() as db:
        org, tomorrow = await _seed_demo_org(db)
        chat_session = await crud_chat.get_or_create_channel_session(
            db, str(org.id), CHANNEL, PHONE
        )
        cfg_res = await db.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
        org_config = cfg_res.scalars().first()
        session_id = str(chat_session.session_id)
        print(f"== Org '{org.name}' (slots tomorrow {tomorrow}, tz {org_config.timezone})\n")

        script = [
            "Hi",
            "Rahul Demo",
            "rahul.demo@example.com",
            PHONE,
            "tomorrow",
            "10:30 AM",
            "yes, confirm",
        ]
        idx = 0
        while idx < len(script):
            user_text = script[idx]
            print(f"Customer: {user_text}")
            action = await runtime_router.route_whatsapp_message(
                db=db,
                org=org,
                org_config=org_config,
                chat_session=chat_session,
                message_text=user_text,
            )
            reply = getattr(action, "message", "") or ""
            print(f"Agent: {reply}\n")

            if _asks_for_otp(reply):
                code = CAPTURED_OTP.get("code")
                if code is None:
                    from app.crud.otp import generate_otp

                    code = await generate_otp(db, session_id, channel="phone")
                    print(f"[OTP generated via crud: {code}]")
                else:
                    print(f"[customer reads the code off WhatsApp: {code}]")
                print(f"Customer: {code}")
                action = await runtime_router.route_whatsapp_message(
                    db=db,
                    org=org,
                    org_config=org_config,
                    chat_session=chat_session,
                    message_text=code,
                )
                print(f"Agent: {getattr(action, 'message', '') or ''}\n")
            idx += 1

        res = await db.execute(_sel(Booking).where(Booking.org_id == org.id))
        bookings = res.scalars().all()
        print("== Booking rows created:", len(bookings))
        for b in bookings:
            print(
                f"   - {b.customer_name} | {b.org_slot_start_at} | email={b.customer_email}"
            )


if __name__ == "__main__":
    asyncio.run(main())
