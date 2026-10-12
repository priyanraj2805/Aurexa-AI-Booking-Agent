"""Booking reminders — 24h email + 1h email (Feature 4).

Runs inside the existing 5-minute cleanup loop (app/core/tasks.py).
No DB migration: dedupe via email_delivery_logs channel
(booking_reminder_24h / booking_reminder_1h with payload.booking_id),
prefs via OrgConfig.custom_prompts.reminders so old orgs keep working.

Windows (tolerant to the 5-min tick):
- 24h: start in [now+23h, now+25h]
- 1h:  start in [now+50min, now+70min]
Only SCHEDULED bookings with a customer email are considered.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.config import OrgConfig
from app.models.email_delivery import EmailDeliveryLog

logger = logging.getLogger(__name__)

CHANNEL_24H = "booking_reminder_24h"
CHANNEL_1H = "booking_reminder_1h"


def reminder_prefs(custom_prompts: Any) -> dict:
    """Org reminder prefs with safe defaults (both on)."""
    if not isinstance(custom_prompts, dict):
        return {"email_24h": True, "email_1h": True}
    raw = custom_prompts.get("reminders")
    if not isinstance(raw, dict):
        return {"email_24h": True, "email_1h": True}
    return {
        "email_24h": bool(raw.get("email_24h", True)),
        "email_1h": bool(raw.get("email_1h", True)),
    }


async def _already_sent(db: AsyncSession, booking_id: str, channel: str) -> bool:
    res = await db.execute(
        select(EmailDeliveryLog.id).where(
            and_(
                EmailDeliveryLog.channel == channel,
                EmailDeliveryLog.status.in_(["pending", "sent"]),
            )
        ).limit(2000)
    )
    # Payload scan in Python (JSONB contains would need pg-specific ops;
    # the table is small per tick so this stays cheap).
    ids = res.scalars().all()
    if not ids:
        return False
    check = await db.execute(
        select(EmailDeliveryLog.payload).where(EmailDeliveryLog.id.in_(list(ids)))
    )
    for (payload,) in check.all():
        if isinstance(payload, dict) and str(payload.get("booking_id") or "") == str(booking_id):
            # Channel already narrowed above per call, so any payload hit = sent.
            return True
    return False


def _render_subject(kind: str, org_name: str) -> str:
    if kind == "24h":
        return f"Reminder: your appointment tomorrow with {org_name}"
    return f"Reminder: your appointment in 1 hour with {org_name}"


def _render_body(kind: str, booking: Booking, org_name: str) -> str:
    start = getattr(booking, "user_slot_start_at", None) or getattr(booking, "org_slot_start_at", None)
    when = ""
    try:
        when = start.strftime("%A, %B %d at %I:%M %p") if start else ""
    except Exception:
        when = ""
    name = getattr(booking, "customer_name", "") or "there"
    window = "tomorrow" if kind == "24h" else "in about an hour"
    meeting = ""
    try:
        if getattr(booking, "meeting_link_url", None):
            meeting = f'<p style="margin:0 0 12px;color:#334155;">Meeting link: <a href="{booking.meeting_link_url}">{booking.meeting_link_url}</a></p>'
    except Exception:
        meeting = ""
    return f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:24px;border:1px solid #e2e8f0;border-radius:12px;">
      <h2 style="margin:0 0 12px;color:#0f172a;">Appointment {window}</h2>
      <p style="margin:0 0 12px;color:#334155;">Hi {name},</p>
      <p style="margin:0 0 12px;color:#334155;">This is a friendly reminder of your appointment <strong>{when}</strong> with <strong>{org_name}</strong>.</p>
      {meeting}
      <p style="margin:16px 0 0;color:#64748b;font-size:12px;">Need to change it? Just reply to this email or message us in chat.</p>
    </div>
    """


async def send_due_booking_reminders(db: AsyncSession) -> int:
    """Send due 24h/1h reminders. Returns count sent. Never raises."""
    try:
        now = datetime.now(timezone.utc)
        win_24_start = now + timedelta(hours=23)
        win_24_end = now + timedelta(hours=25)
        win_1_start = now + timedelta(minutes=50)
        win_1_end = now + timedelta(minutes=70)

        res = await db.execute(
            select(Booking).where(
                and_(
                    Booking.attendance_status == "SCHEDULED",
                    Booking.customer_email.isnot(None),
                    Booking.user_slot_start_at.isnot(None),
                )
            ).limit(500)
        )
        bookings = list(res.scalars().all())
        if not bookings:
            return 0

        # Org prefs + names in one pass.
        org_ids = list({b.org_id for b in bookings})
        cfg_res = await db.execute(select(OrgConfig).where(OrgConfig.org_id.in_(org_ids)))
        cfg_by_org = {str(c.org_id): c for c in cfg_res.scalars().all()}
        try:
            from app.models.organization import Organization

            org_res = await db.execute(select(Organization).where(Organization.id.in_(org_ids)))
            name_by_org = {str(o.id): (o.name or "our team") for o in org_res.scalars().all()}
        except Exception:
            name_by_org = {}

        from app.core.email_delivery_service import send_email_with_failover

        sent = 0
        for booking in bookings:
            try:
                start = getattr(booking, "user_slot_start_at", None) or getattr(booking, "org_slot_start_at", None)
                if start is None:
                    continue
                if getattr(start, "tzinfo", None) is None:
                    start = start.replace(tzinfo=timezone.utc)
                kind: Optional[str] = None
                if win_24_start <= start <= win_24_end:
                    kind = "24h"
                elif win_1_start <= start <= win_1_end:
                    kind = "1h"
                if kind is None:
                    continue
                prefs = reminder_prefs(getattr(cfg_by_org.get(str(booking.org_id)), "custom_prompts", None))
                if kind == "24h" and not prefs["email_24h"]:
                    continue
                if kind == "1h" and not prefs["email_1h"]:
                    continue
                channel = CHANNEL_24H if kind == "24h" else CHANNEL_1H
                if await _already_sent(db, str(booking.id), channel):
                    continue
                email = str(getattr(booking, "customer_email", "") or "").strip()
                if not email:
                    continue
                org_name = name_by_org.get(str(booking.org_id), "our team")
                await send_email_with_failover(
                    org_id=booking.org_id,
                    channel=channel,
                    to_email=email,
                    subject=_render_subject(kind, org_name),
                    body_html=_render_body(kind, booking, org_name),
                    payload={"booking_id": str(booking.id), "kind": kind},
                )
                sent += 1
            except Exception as one_err:
                logger.warning("reminder.send_one.failed: %s", one_err)
                continue
        if sent:
            logger.info("reminders.sent count=%s", sent)
        return sent
    except Exception as e:
        logger.warning("reminders.tick.failed: %s", e)
        return 0
