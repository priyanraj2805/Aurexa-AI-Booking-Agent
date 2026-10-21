"""Live WhatsApp off-topic check — a knowledge question must be ANSWERED,
not hijacked by the department list.
"""
import asyncio
import logging
import sys

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.WARNING, stream=sys.stdout)

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

PHONE = "+919999550002"
CHANNEL = "whatsapp_cloud"


async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            _sel(Organization).where(Organization.name == "Dotstark technologies")
        )
        org = res.scalars().first()
        cfg_res = await db.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
        org_config = cfg_res.scalars().first()

        chat_session = await crud_chat.get_or_create_channel_session(
            db, str(org.id), CHANNEL, PHONE
        )
        chat_session.collected_data = {}
        chat_session.missing_fields = []
        chat_session.current_intent = "START_BOOKING"
        chat_session.session_status = "ACTIVE"
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

        for user_text in (
            "What are your opening hours?",
            "Do you offer physiotherapy?",
        ):
            print(f"Customer: {user_text}")
            action = await runtime_router.route_whatsapp_message(
                db=db,
                org=org,
                org_config=org_config,
                chat_session=chat_session,
                message_text=user_text,
            )
            print(f"Agent: {getattr(action, 'message', '') or ''}\n")


if __name__ == "__main__":
    asyncio.run(main())
