"""Live WhatsApp department-picker check.

Drives the REAL runtime_router (the exact function the WhatsApp webhook
uses) against the tenant org that has departments, and prints the
transcript so the department offer + pick can be verified end to end.

Run inside the backend container:
  docker exec booking_agent-backend-1 python /app/wa_dept_check.py
"""
import asyncio
import logging
import sys
import uuid

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
for _n in ("app", "app.engine.langgraph_orchestrator"):
    logging.getLogger(_n).setLevel(logging.INFO)

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
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.service_unit import ServiceUnit

PHONE = "+919999550001"
CHANNEL = "whatsapp_cloud"


async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            _sel(Organization).where(
                Organization.name == "Dotstark technologies"
            )
        )
        org = res.scalars().first()
        if org is None:
            raise RuntimeError("org with departments not found")
        cfg_res = await db.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
        org_config = cfg_res.scalars().first()
        units_res = await db.execute(
            _sel(ServiceUnit).where(ServiceUnit.org_id == org.id, ServiceUnit.is_active.is_(True))
        )
        units = units_res.scalars().all()
        print(f"== org '{org.name}' — {len(units)} active departments")

        chat_session = await crud_chat.get_or_create_channel_session(
            db, str(org.id), CHANNEL, PHONE
        )
        # Fresh session state so the offer is not suppressed by an old flag.
        chat_session.collected_data = {}
        chat_session.missing_fields = []
        chat_session.current_intent = "START_BOOKING"
        chat_session.session_status = "ACTIVE"
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        print(f"== session {chat_session.session_id}\n")

        script = [
            "Hi, I want to book an appointment",
            "Cardiology",
            "Rahul Demo",
            "rahul.demo@example.com",
            "+919999550001",
            "22 October 2026",
            "Dr Aarav Sharma",
            "10:30 AM",
        ]
        for user_text in script:
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
            print(
                f"   [directive={getattr(action, 'widget_directive', '')} "
                f"unit={(chat_session.collected_data or {}).get('unit_name')!r} "
                f"name={(chat_session.collected_data or {}).get('name')!r} "
                f"date={(chat_session.collected_data or {}).get('date')!r}]\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
