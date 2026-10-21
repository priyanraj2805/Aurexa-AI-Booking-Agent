"""
WhatsApp booking lifecycle extras: waitlist, abandoned-booking recovery,
self-service manage link.

Three revenue features that only make sense on WhatsApp:
- WAITLIST: the customer wanted a slot that was full. Keep the demand,
  auto-notify when a slot opens.
- ABANDONED: the customer started booking and vanished. WhatsApp is the
  one channel where a business may message them first (with a template).
- MANAGE LINK: a signed one-tap link so the customer can reschedule or
  cancel without typing natural language (and without another LLM turn).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select as _sel

from app.models.booking import Booking
from app.models.session import ChatSession
from app.models.whatsapp_engagement import BookingWaitlist

logger = logging.getLogger(__name__)


# ── Signed manage-booking token ──────────────────────────────────────
# Stateless: HMAC over booking id + expiry. No table needed, and the link
# can be re-issued for the same booking at any time.
_MANAGE_TOKEN_SECRET_ENV = "BOOKING_MANAGE_TOKEN_SECRET"


def _secret() -> str:
    from app.core.config import settings

    raw = (
        getattr(settings, _MANAGE_TOKEN_SECRET_ENV, "")
        or getattr(settings, "JWT_SECRET_KEY", "")
        or "dotstark-manage-default"
    )
    return str(raw)


def make_manage_token(booking_id: str, *, days_valid: int = 30) -> str:
    """Signed token for the self-service manage link."""
    exp = int((datetime.now(timezone.utc) + timedelta(days=days_valid)).timestamp())
    payload = f"{booking_id}.{exp}"
    sig = hmac.new(
        _secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{exp}.{sig}"


def verify_manage_token(booking_id: str, token: str) -> bool:
    """True when the token is valid for this booking and not expired."""
    try:
        raw = str(token or "").strip()
        if "." not in raw:
            return False
        exp_raw, sig = raw.split(".", 1)
        exp = int(exp_raw)
        if exp < int(datetime.now(timezone.utc).timestamp()):
            return False
        payload = f"{booking_id}.{exp}"
        expected = hmac.new(
            _secret().encode(), payload.encode(), hashlib.sha256
        ).hexdigest()[:32]
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def manage_url(booking_id: str, *, base_url: str = "", days_valid: int = 30) -> str:
    token = make_manage_token(booking_id, days_valid=days_valid)
    base = (base_url or "").rstrip("/")
    if not base:
        from app.core.config import settings

        base = (getattr(settings, "PUBLIC_API_BASE", "") or "").rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
    return f"{base}/api/v1/booking/manage?booking_id={booking_id}&token={token}"


# ── Waitlist → notify on slot open ───────────────────────────────────
async def notify_waitlist_on_slot_open(
    db: AsyncSession, org_id: Any, *, target_date: Any = None
) -> int:
    """Notify waitlisted customers when a slot frees up.

    Returns how many were notified. Never raises.
    """
    sent = 0
    try:
        from app.services import whatsapp_engagement as _eng

        conds = [
            BookingWaitlist.org_id == org_id,
            BookingWaitlist.status == "waiting",
        ]
        if target_date is not None:
            conds.append(BookingWaitlist.target_date == target_date)
        rows = (
            await db.execute(_sel(BookingWaitlist).where(*conds).limit(50))
        ).scalars().all()
        for row in rows:
            phone = "".join(c for c in str(row.phone or "") if c.isdigit())
            if not phone:
                continue
            if await _eng.is_opted_out(db, org_id, phone):
                continue
            try:
                from app.core import whatsapp_templates as _tpl

                res = await _tpl.send_waitlist_joined(
                    phone, str(row.name or ""), "your requested date", "our team"
                )
                if res.get("success"):
                    row.status = "notified"
                    row.notified_at = datetime.now(timezone.utc)
                    await db.commit()
                    sent += 1
            except Exception:
                continue
    except Exception:
        logger.warning("waitlist notify failed", exc_info=True)
    return sent


# ── Abandoned booking recovery ───────────────────────────────────────
# A session that started booking (has contact details) but never
# completed, and has been quiet for a while. WhatsApp is the only channel
# where the business may message first, so this is where the demand
# actually comes back.
ABANDONED_MIN_AGE_MINUTES = 60


async def find_abandoned_sessions(
    db: AsyncSession, org_id: Any, *, min_age_minutes: int = ABANDONED_MIN_AGE_MINUTES
) -> List[ChatSession]:
    """WhatsApp sessions with booking started but never confirmed."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age_minutes)
        res = await db.execute(
            _sel(ChatSession)
            .where(
                ChatSession.org_id == org_id,
                ChatSession.channel.in_(("whatsapp", "whatsapp_cloud")),
                ChatSession.session_status == "ACTIVE",
                ChatSession.last_activity_at.isnot(None),
                ChatSession.last_activity_at < cutoff,
            )
            .limit(200)
        )
        sessions = list(res.scalars().all())
    except Exception:
        logger.warning("abandoned session lookup failed", exc_info=True)
        return []
    out: List[ChatSession] = []
    for s in sessions:
        state = s.state_data if isinstance(s.state_data, dict) else {}
        if not str(state.get("phone") or "").strip():
            continue
        # Never touch a session that already has a live booking.
        try:
            live = (
                await db.execute(
                    _sel(func.count(Booking.id)).where(
                        Booking.session_id == s.id,
                        Booking.attendance_status == "SCHEDULED",
                    )
                )
            ).scalar() or 0
        except Exception:
            live = 0
        if live:
            continue
        out.append(s)
    return out


async def send_abandoned_nudge(
    db: AsyncSession, org_id: Any, session: ChatSession
) -> bool:
    """One nudge per abandoned session. Dedupe via the funnel/lead state."""
    try:
        from app.services import whatsapp_engagement as _eng

        state = session.state_data if isinstance(session.state_data, dict) else {}
        phone = "".join(c for c in str(state.get("phone") or "") if c.isdigit())
        if not phone:
            return False
        if await _eng.is_opted_out(db, org_id, phone):
            return False
        if await _eng.already_delivered(
            db, session.id, "abandoned_nudge"
        ):
            return False
        from app.core import whatsapp_templates as _tpl

        res = await _tpl.send_abandoned_booking(
            phone, str(state.get("name") or ""), "our team"
        )
        await _eng.log_delivery(
            db, org_id, booking_id=session.id, channel="abandoned_nudge",
            phone=phone, template_name=_tpl.TEMPLATE_ABANDONED_BOOKING,
            status="sent" if res.get("success") else "failed",
            error=str(res.get("error") or "")[:500],
            provider_message_id=str(res.get("wamid") or ""),
        )
        return bool(res.get("success"))
    except Exception as exc:
        logger.warning("abandoned nudge failed: %s", exc)
        return False


# ── WhatsApp campaign (broadcast) ────────────────────────────────────
# A tenant wants to reach its customers on WhatsApp. Rules:
#   * template-based (Meta requires it outside the 24h window)
#   * only customers who actually interacted (opted-in by messaging us)
#   * opt-outs are skipped, always
#   * one message per phone per campaign (dedupe)
async def campaign_recipients(
    db: AsyncSession, org_id: Any, *, limit: int = 500
) -> List[str]:
    """Phones that messaged this org before and have not opted out."""
    try:
        from app.models.session import ChatSession
        from app.services import whatsapp_engagement as _eng

        res = await db.execute(
            _sel(ChatSession)
            .where(
                ChatSession.org_id == org_id,
                ChatSession.channel.in_(("whatsapp", "whatsapp_cloud")),
            )
            .limit(2000)
        )
        phones: List[str] = []
        for row in res.scalars().all():
            state = row.state_data if isinstance(row.state_data, dict) else {}
            phone = "".join(c for c in str(state.get("phone") or "") if c.isdigit())
            if not phone or phone in phones:
                continue
            phones.append(phone)
        out: List[str] = []
        for phone in phones:
            if await _eng.is_opted_out(db, org_id, phone):
                continue
            out.append(phone)
            if len(out) >= limit:
                break
        return out
    except Exception:
        logger.warning("campaign recipients lookup failed", exc_info=True)
        return []


async def send_campaign(
    db: AsyncSession,
    org_id: Any,
    *,
    template_name: str,
    body_params: List[str],
    limit: int = 500,
) -> dict:
    """Send an approved template to the org's WhatsApp customers."""
    result = {"sent": 0, "skipped": 0, "failed": 0}
    try:
        from app.core import whatsapp_templates as _tpl
        from app.services import whatsapp_engagement as _eng

        for phone in await campaign_recipients(db, org_id, limit=limit):
            res = await _tpl.send_cloud_template(phone, template_name, body_params)
            if res.get("success"):
                result["sent"] += 1
                await _eng.log_delivery(
                    db, org_id, channel=f"campaign:{template_name}",
                    phone=phone, template_name=template_name, status="sent",
                    provider_message_id=str(res.get("wamid") or ""),
                )
            elif res.get("mock"):
                result["skipped"] += 1
            else:
                result["failed"] += 1
                await _eng.log_delivery(
                    db, org_id, channel=f"campaign:{template_name}",
                    phone=phone, template_name=template_name, status="failed",
                    error=str(res.get("error") or "")[:500],
                )
    except Exception:
        logger.warning("campaign send failed", exc_info=True)
    logger.info(
        "whatsapp campaign sent=%s skipped=%s failed=%s",
        result["sent"], result["skipped"], result["failed"],
    )
    return result
