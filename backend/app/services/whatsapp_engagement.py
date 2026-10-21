"""
Centralized WhatsApp engagement services.

Everything WhatsApp-specific that is NOT transport lives here:
- opt-out (STOP) handling
- 24-hour customer-service window tracking
- language detection
- lead capture
- waitlist
- funnel events
- delivery logging

The transports (whatsapp_qr / whatsapp_webhook) call into this module so
the QR and Meta Cloud paths can never diverge.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete as _delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select as _sel

from app.models.whatsapp_engagement import (
    BookingFunnelEvent,
    BookingLead,
    BookingWaitlist,
    WhatsAppDeliveryLog,
    WhatsAppOptOut,
)

logger = logging.getLogger(__name__)

# ── Opt-out ───────────────────────────────────────────────────────────
# Meta requires a STOP to be honoured immediately. Answering again risks
# the business number being banned, so these are checked before ANY other
# processing on an inbound message.
STOP_KEYWORDS = {
    "stop", "unsubscribe", "unsubscribed", "cancel updates",
    "end", "quit", "leave", "opt out", "optout", "remove me",
    "stop notifications", "stop messages", "no more messages",
}

# Opt-out confirmation (sent once, then silence).
STOP_CONFIRMATION = (
    "You've been unsubscribed and won't receive further messages from us. "
    "Reply START to subscribe again."
)
START_KEYWORDS = {"start", "subscribe", "resume", "opt in", "optin"}


def is_stop_request(text: str) -> bool:
    """True when the message is a stop/unsubscribe request."""
    cleaned = re.sub(r"[!.,?;:]+$", "", str(text or "").strip().lower())
    if not cleaned:
        return False
    if cleaned in STOP_KEYWORDS:
        return True
    # "please stop sending me messages" style.
    if cleaned.startswith("stop ") or cleaned.startswith("please stop"):
        return True
    if "unsubscribe" in cleaned and len(cleaned) <= 40:
        return True
    return False


def is_start_request(text: str) -> bool:
    cleaned = re.sub(r"[!.,?;:]+$", "", str(text or "").strip().lower())
    return cleaned in START_KEYWORDS


async def is_opted_out(db: AsyncSession, org_id: Any, phone: str) -> bool:
    if not phone:
        return False
    try:
        row = (
            await db.execute(
                _sel(WhatsAppOptOut.id).where(
                    WhatsAppOptOut.org_id == org_id,
                    WhatsAppOptOut.phone == str(phone),
                )
            )
        ).first()
        return row is not None
    except Exception:
        logger.warning("opt-out lookup failed", exc_info=True)
        return False


async def record_opt_out(
    db: AsyncSession,
    org_id: Any,
    phone: str,
    *,
    reason: str = "stop",
    session_id: Any = None,
    channel: str = "whatsapp",
) -> bool:
    """Persist an opt-out. Returns True when a NEW opt-out was recorded."""
    if not phone:
        return False
    try:
        existing = (
            await db.execute(
                _sel(WhatsAppOptOut.id).where(
                    WhatsAppOptOut.org_id == org_id,
                    WhatsAppOptOut.phone == str(phone),
                    WhatsAppOptOut.channel == channel,
                )
            )
        ).first()
        if existing is not None:
            return False
        db.add(
            WhatsAppOptOut(
                org_id=org_id,
                phone=str(phone),
                channel=channel,
                reason=reason,
                source_session_id=session_id,
            )
        )
        await db.commit()
        logger.info("whatsapp opt-out recorded org=%s phone=%s***", org_id, str(phone)[-4:])
        return True
    except Exception:
        logger.warning("opt-out record failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return False


async def clear_opt_out(
    db: AsyncSession, org_id: Any, phone: str, channel: str = "whatsapp"
) -> bool:
    """Re-subscribe a previously opted-out number."""
    try:
        await db.execute(
            _delete(WhatsAppOptOut).where(
                WhatsAppOptOut.org_id == org_id,
                WhatsAppOptOut.phone == str(phone),
                WhatsAppOptOut.channel == channel,
            )
        )
        await db.commit()
        return True
    except Exception:
        logger.warning("opt-out clear failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return False


# ── 24-hour customer-service window ───────────────────────────────────
# Meta only allows free-form replies inside 24h of the customer's last
# message. Outside that window a template is mandatory — sending text
# fails, so we track the window and route accordingly.
WHATSAPP_WINDOW_HOURS = 24


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        from dateutil.parser import parse as _parse

        parsed = _parse(str(raw))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def within_service_window(last_inbound_at: Any, now: Optional[datetime] = None) -> bool:
    """True when a free-form reply is still allowed (24h since last inbound)."""
    last = _parse_dt(last_inbound_at)
    if last is None:
        # No inbound yet — treat as OUTSIDE the window so a business-initiated
        # message always uses a template. Fail safe, not fail open.
        return False
    now = now or datetime.now(timezone.utc)
    return (now - last) <= timedelta(hours=WHATSAPP_WINDOW_HOURS)


# ── Language detection ────────────────────────────────────────────────
# Cheap script detection — no model call, no API. Good enough to pick a
# template language and to tell the agent which language to reply in.
_SCRIPT_RANGES: List[Tuple[int, int, str]] = [
    (0x0900, 0x097F, "hi"),   # Devanagari
    (0x0980, 0x09FF, "bn"),   # Bengali
    (0x0A00, 0x0A7F, "gu"),   # Gujarati
    (0x0A80, 0x0AFF, "pa"),   # Punjabi
    (0x0B00, 0x0B7F, "or"),   # Odia
    (0x0B80, 0x0BFF, "ta"),   # Tamil
    (0x0C00, 0x0C7F, "te"),   # Telugu
    (0x0C80, 0x0CFF, "kn"),   # Kannada
    (0x0D00, 0x0D7F, "ml"),   # Malayalam
    (0x0E00, 0x0E7F, "th"),   # Thai
    (0x0600, 0x06FF, "ar"),   # Arabic
    (0x4E00, 0x9FFF, "zh"),   # CJK
    (0x3040, 0x30FF, "ja"),   # Kana
    (0xAC00, 0xD7AF, "ko"),   # Hangul
]


def detect_language(text: str) -> str:
    """Best-effort language code for a message. 'en' when unknown."""
    raw = str(text or "")
    if not raw.strip():
        return "en"
    counts: Dict[str, int] = {}
    for ch in raw:
        cp = ord(ch)
        for lo, hi, code in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if not counts:
        return "en"
    return max(counts.items(), key=lambda kv: kv[1])[0]


# ── Lead capture ──────────────────────────────────────────────────────
async def capture_lead(
    db: AsyncSession,
    org_id: Any,
    *,
    session_id: Any = None,
    phone: str = "",
    name: str = "",
    email: str = "",
    channel: str = "whatsapp",
    intent: str = "book",
    notes: str = "",
    context: Optional[dict] = None,
) -> Optional[BookingLead]:
    """Record (or refresh) a lead for a conversation.

    A session is the lead key: one enquiry per session, refreshed as more
    detail arrives. Never raises.
    """
    try:
        existing = None
        if session_id:
            existing = (
                await db.execute(
                    _sel(BookingLead).where(
                        BookingLead.session_id == session_id,
                        BookingLead.org_id == org_id,
                    )
                )
            ).scalars().first()
        if existing is not None:
            if phone:
                existing.phone = phone
            if name:
                existing.name = name
            if email:
                existing.email = email
            if notes:
                existing.notes = notes
            if context:
                existing.context = context
            await db.commit()
            return existing
        lead = BookingLead(
            org_id=org_id,
            session_id=session_id,
            phone=phone or None,
            name=name or None,
            email=email or None,
            channel=channel,
            intent=intent,
            notes=notes or None,
            context=context or None,
            status="new",
        )
        db.add(lead)
        await db.commit()
        return lead
    except Exception:
        logger.warning("lead capture failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return None


async def mark_lead_converted(
    db: AsyncSession, org_id: Any, session_id: Any, booking_id: Any = None
) -> None:
    try:
        lead = (
            await db.execute(
                _sel(BookingLead).where(
                    BookingLead.session_id == session_id,
                    BookingLead.org_id == org_id,
                )
            )
        ).scalars().first()
        if lead is not None:
            lead.status = "converted"
            if booking_id is not None:
                lead.booking_id = booking_id
            await db.commit()
    except Exception:
        logger.warning("lead convert mark failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass


# ── Waitlist ──────────────────────────────────────────────────────────
async def join_waitlist(
    db: AsyncSession,
    org_id: Any,
    *,
    session_id: Any = None,
    unit_id: Any = None,
    provider_id: Any = None,
    phone: str = "",
    name: str = "",
    email: str = "",
    target_date: Any = None,
) -> Optional[BookingWaitlist]:
    """Put a customer on the waitlist for a date. Idempotent per session."""
    try:
        if session_id:
            existing = (
                await db.execute(
                    _sel(BookingWaitlist).where(
                        BookingWaitlist.session_id == session_id,
                        BookingWaitlist.org_id == org_id,
                        BookingWaitlist.status == "waiting",
                    )
                )
            ).scalars().first()
            if existing is not None:
                return existing
        row = BookingWaitlist(
            org_id=org_id,
            session_id=session_id,
            unit_id=unit_id,
            provider_id=provider_id,
            phone=phone or None,
            name=name or None,
            email=email or None,
            target_date=target_date,
            status="waiting",
        )
        db.add(row)
        await db.commit()
        return row
    except Exception:
        logger.warning("waitlist join failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass
        return None


async def waiting_entries(db: AsyncSession, org_id: Any) -> List[BookingWaitlist]:
    try:
        return list(
            (
                await db.execute(
                    _sel(BookingWaitlist).where(
                        BookingWaitlist.org_id == org_id,
                        BookingWaitlist.status == "waiting",
                    )
                )
            ).scalars().all()
        )
    except Exception:
        logger.warning("waitlist read failed", exc_info=True)
        return []


# ── Funnel ────────────────────────────────────────────────────────────
async def track_step(
    db: AsyncSession,
    org_id: Any,
    step: str,
    *,
    session_id: Any = None,
    channel: str = "whatsapp",
    outcome: str = "entered",
) -> None:
    """Record a funnel step. Best-effort, never raises."""
    try:
        db.add(
            BookingFunnelEvent(
                org_id=org_id,
                session_id=session_id,
                channel=channel,
                step=step,
                outcome=outcome,
            )
        )
        await db.commit()
    except Exception:
        logger.debug("funnel track failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass


async def funnel_breakdown(
    db: AsyncSession, org_id: Any, *, days: int = 30, channel: Optional[str] = None
) -> Dict[str, Any]:
    """Per-step entered/completed counts for the dashboard."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 30)))
    try:
        conds = [
            BookingFunnelEvent.org_id == org_id,
            BookingFunnelEvent.created_at >= since,
        ]
        if channel:
            conds.append(BookingFunnelEvent.channel == channel)
        rows = (
            await db.execute(
                _sel(
                    BookingFunnelEvent.step,
                    BookingFunnelEvent.outcome,
                    func.count(BookingFunnelEvent.id),
                )
                .where(*conds)
                .group_by(BookingFunnelEvent.step, BookingFunnelEvent.outcome)
            )
        ).all()
        steps: Dict[str, Dict[str, int]] = {}
        for step, outcome, count in rows:
            entry = steps.setdefault(str(step), {"entered": 0, "completed": 0, "abandoned": 0})
            key = str(outcome) if str(outcome) in entry else "entered"
            entry[key] = int(count or 0)
        for entry in steps.values():
            started = entry.get("entered", 0)
            completed = entry.get("completed", 0)
            entry["conversion"] = round(completed / started, 3) if started else 0.0
        return {"days": days, "channel": channel, "steps": steps}
    except Exception:
        logger.warning("funnel breakdown failed", exc_info=True)
        return {"days": days, "channel": channel, "steps": {}}


# ── Delivery log ──────────────────────────────────────────────────────
async def log_delivery(
    db: AsyncSession,
    org_id: Any,
    *,
    booking_id: Any = None,
    channel: str,
    phone: str = "",
    template_name: str = "",
    status: str = "sent",
    error: str = "",
    provider_message_id: str = "",
) -> None:
    try:
        db.add(
            WhatsAppDeliveryLog(
                org_id=org_id,
                booking_id=booking_id,
                channel=channel,
                phone=phone or None,
                template_name=template_name or None,
                status=status,
                error=error[:2000] if error else None,
                provider_message_id=provider_message_id or None,
            )
        )
        await db.commit()
    except Exception:
        logger.debug("delivery log failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass


async def already_delivered(db: AsyncSession, booking_id: Any, channel: str) -> bool:
    try:
        row = (
            await db.execute(
                _sel(WhatsAppDeliveryLog.id).where(
                    WhatsAppDeliveryLog.booking_id == booking_id,
                    WhatsAppDeliveryLog.channel == channel,
                    WhatsAppDeliveryLog.status == "sent",
                )
            )
        ).first()
        return row is not None
    except Exception:
        logger.warning("delivery lookup failed", exc_info=True)
        return False
