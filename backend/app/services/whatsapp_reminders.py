"""
WhatsApp booking reminders (24h + 1h) and lifecycle notifications.

Email reminders already exist in `app/services/reminders.py` — but they
only fire for customers with an email, which skips the WhatsApp-first
majority of bookings. This module sends the same two nudges over WhatsApp,
deduped through `WhatsAppDeliveryLog` so a reminder never goes twice.

Messages go out as Meta templates (required for business-initiated
messages outside the 24h service window).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select as _sel

from app.models.booking import Booking
from app.models.config import OrgConfig
from app.services import whatsapp_engagement as _eng

logger = logging.getLogger(__name__)

CHANNEL_24H = "reminder_24h"
CHANNEL_1H = "reminder_1h"


def _naive_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _slot_start(booking: Booking) -> Optional[datetime]:
    return _naive_utc(
        getattr(booking, "user_slot_start_at", None)
        or getattr(booking, "org_slot_start_at", None)
    )


def _slot_label(booking: Booking) -> str:
    start = _slot_start(booking)
    if start is None:
        return ""
    try:
        return start.strftime("%A, %d %B at %I:%M %p")
    except Exception:
        return start.isoformat()


def _booking_phone(booking: Booking) -> str:
    return "".join(c for c in str(getattr(booking, "customer_phone", "") or "") if c.isdigit())


async def _whatsapp_bookings(
    db: AsyncSession, window_start: datetime, window_end: datetime
) -> List[Booking]:
    """Bookings starting in the window that were made over WhatsApp."""
    try:
        from app.models.session import ChatSession

        res = await db.execute(
            _sel(Booking)
            .join(ChatSession, ChatSession.id == Booking.session_id)
            .where(
                Booking.attendance_status == "SCHEDULED",
                Booking.customer_phone.isnot(None),
                Booking.user_slot_start_at.isnot(None),
                ChatSession.channel.in_(("whatsapp", "whatsapp_cloud")),
            )
            .limit(500)
        )
        rows = list(res.scalars().all())
    except Exception:
        logger.warning("whatsapp reminder booking lookup failed", exc_info=True)
        return []
    out = []
    for b in rows:
        start = _slot_start(b)
        if start is not None and window_start <= start <= window_end:
            out.append(b)
    return out


async def _org_names(db: AsyncSession, org_ids: List[Any]) -> Dict[str, str]:
    if not org_ids:
        return {}
    try:
        from app.models.organization import Organization

        res = await db.execute(
            _sel(Organization).where(Organization.id.in_(org_ids))
        )
        return {str(o.id): (o.name or "our team") for o in res.scalars().all()}
    except Exception:
        return {}


async def _send(
    db: AsyncSession,
    booking: Booking,
    *,
    kind: str,
    business_name: str,
) -> bool:
    """Send one reminder. Returns True when a message was actually sent."""
    phone = _booking_phone(booking)
    if not phone:
        return False
    if await _eng.already_delivered(db, str(booking.id), kind):
        return False
    if await _eng.is_opted_out(db, booking.org_id, phone):
        await _eng.log_delivery(
            db, booking.org_id, booking_id=booking.id, channel=kind,
            phone=phone, status="skipped", error="opted out",
        )
        return False
    name = str(getattr(booking, "customer_name", "") or "")
    when = _slot_label(booking)
    template_name = ""
    try:
        from app.core import whatsapp_templates as _tpl

        res = await _tpl.send_booking_reminder(
            phone, name, when, business_name, kind=kind
        )
        template_name = (
            _tpl.TEMPLATE_BOOKING_REMINDER_24H
            if kind == "24h"
            else _tpl.TEMPLATE_BOOKING_REMINDER_1H
        )
        ok = bool(res.get("success"))
        if not ok:
            await _eng.log_delivery(
                db, booking.org_id, booking_id=booking.id, channel=kind,
                phone=phone, template_name=template_name, status="failed",
                error=str(res.get("error") or res.get("reason") or "")[:500],
            )
            return False
        await _eng.log_delivery(
            db, booking.org_id, booking_id=booking.id, channel=kind,
            phone=phone, template_name=template_name, status="sent",
            provider_message_id=str(res.get("wamid") or ""),
        )
        return True
    except Exception as exc:
        logger.warning("whatsapp reminder send failed booking=%s: %s", booking.id, exc)
        await _eng.log_delivery(
            db, booking.org_id, booking_id=booking.id, channel=kind,
            phone=phone, status="failed", error=str(exc)[:500],
        )
        return False


async def send_due_whatsapp_reminders(db: AsyncSession) -> int:
    """Send due 24h/1h WhatsApp reminders. Returns count sent. Never raises."""
    try:
        now = datetime.now(timezone.utc)
        win_24 = (now + timedelta(hours=23), now + timedelta(hours=25))
        win_1 = (now + timedelta(minutes=50), now + timedelta(minutes=70))

        sent = 0
        for win, kind in ((win_24, "24h"), (win_1, "1h")):
            bookings = await _whatsapp_bookings(db, win[0], win[1])
            if not bookings:
                continue
            names = await _org_names(db, [b.org_id for b in bookings])
            for booking in bookings:
                try:
                    business = names.get(str(booking.org_id), "our team")
                    if await _send(db, booking, kind=kind, business_name=business):
                        sent += 1
                except Exception:
                    continue
        if sent:
            logger.info("whatsapp reminders sent count=%s", sent)
        return sent
    except Exception as exc:
        logger.warning("whatsapp reminders tick failed: %s", exc)
        return 0


async def send_booking_cancelled_message(
    db: AsyncSession, booking: Booking, *, business_name: str = ""
) -> bool:
    """WhatsApp cancellation notice with the appointment details."""
    try:
        phone = _booking_phone(booking)
        if not phone:
            return False
        if await _eng.is_opted_out(db, booking.org_id, phone):
            return False
        from app.core import whatsapp_templates as _tpl

        res = await _tpl.send_booking_cancelled(
            phone,
            str(getattr(booking, "customer_name", "") or ""),
            _slot_label(booking),
            business_name or "Dotstark Technologies",
        )
        await _eng.log_delivery(
            db, booking.org_id, booking_id=booking.id, channel="cancellation",
            phone=phone, template_name=_tpl.TEMPLATE_BOOKING_CANCELLED,
            status="sent" if res.get("success") else "failed",
            error=str(res.get("error") or "")[:500],
            provider_message_id=str(res.get("wamid") or ""),
        )
        return bool(res.get("success"))
    except Exception as exc:
        logger.warning("whatsapp cancellation notice failed booking=%s: %s", booking.id, exc)
        return False


async def send_booking_confirmation(
    db: AsyncSession, booking: Booking, *, business_name: str = ""
) -> bool:
    """WhatsApp confirmation with the appointment details (rich receipt).

    A signed self-service link follows the template so the customer can
    reschedule or cancel without typing natural language.
    """
    try:
        phone = _booking_phone(booking)
        if not phone:
            return False
        if await _eng.is_opted_out(db, booking.org_id, phone):
            return False
        from app.core import whatsapp_templates as _tpl

        res = await _tpl.send_booking_confirmed(
            phone,
            str(getattr(booking, "customer_name", "") or ""),
            _slot_label(booking),
            business_name or "Dotstark Technologies",
        )
        await _eng.log_delivery(
            db, booking.org_id, booking_id=booking.id, channel="confirmation",
            phone=phone, template_name=_tpl.TEMPLATE_BOOKING_CONFIRMED,
            status="sent" if res.get("success") else "failed",
            error=str(res.get("error") or "")[:500],
            provider_message_id=str(res.get("wamid") or ""),
        )
        # Self-service link (free text is allowed: the customer just messaged).
        try:
            from app.services import whatsapp_lifecycle as _life
            from app.core import whatsapp_cloud as _cloud
            from app.models.whatsapp_connection import WhatsAppConnection
            from sqlalchemy.future import select as _sel

            link = _life.manage_url(str(booking.id))
            body = (
                "Manage your booking (reschedule or cancel) here: " + link
            )
            if res.get("success"):
                try:
                    conn = (
                        await db.execute(
                            _sel(WhatsAppConnection).where(
                                WhatsAppConnection.org_id == booking.org_id
                            )
                        )
                    ).scalars().first()
                    if conn is not None:
                        await _cloud.send_booking_reply(
                            phone, {"type": "text", "body": body}
                        )
                except Exception:
                    logger.debug("manage link send failed", exc_info=True)
        except Exception:
            logger.debug("manage link build failed", exc_info=True)
        return bool(res.get("success"))
    except Exception as exc:
        logger.warning("whatsapp confirmation failed booking=%s: %s", booking.id, exc)
        return False
