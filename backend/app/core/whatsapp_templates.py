"""Booking templates via Meta Cloud API (Marketing, language `en`).

Your 3 approved templates (all Marketing, Active-Quality pending):
  - booking_welcome        -> Hello {{1}}, welcome to {{2}}... [Quick Reply][Book Now]
  - booking_request_email  -> Hello {{1}}... email? + footer Example: name@example.com
  - bookings_request_phone -> Thanks. Could you please share your phone number? + footer

Transport note: booking chat itself runs on the Baileys QR gateway
(`app/core/wa_gateway.py`, `app/api/v1/endpoints/whatsapp_qr.py:qr_inbound`).
Cloud templates send from WHATSAPP_OTP_PHONE_NUMBER_ID (same creds as OTP at
`app/core/otp_delivery/whatsapp_provider.py:19`). That is a second sender
thread — same as OTP today — so we send Cloud for Meta compliance AND keep
the Baileys reply as the conversational fallback. No gateway change needed.
When you migrate to Cloud-only, flip WHATSAPP_BOOKING_TEMPLATES_CLOUD_ONLY.
"""

import logging
import re
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TEMPLATE_LANG_DEFAULT = "en"

TEMPLATE_WELCOME = "booking_welcome"
TEMPLATE_REQUEST_EMAIL = "booking_request_email"
TEMPLATE_REQUEST_PHONE = "bookings_request_phone"  # plural — the Active one

# Lifecycle templates. Meta requires a template for any business-initiated
# message (reminders, confirmations sent outside the 24h window). These must
# exist AND be APPROVED in the Meta template dashboard before they can send.
TEMPLATE_BOOKING_CONFIRMED = "booking_confirmed"
TEMPLATE_BOOKING_REMINDER_24H = "booking_reminder_24h"
TEMPLATE_BOOKING_REMINDER_1H = "booking_reminder_1h"
TEMPLATE_BOOKING_CANCELLED = "booking_cancelled"
TEMPLATE_BOOKING_RESCHEDULED = "booking_rescheduled"
TEMPLATE_WAITLIST_JOINED = "booking_waitlist_joined"
TEMPLATE_ABANDONED_BOOKING = "booking_abandoned"

# Kill switch: false = keep pure Baileys behavior, no Cloud calls.
TEMPLATES_ENABLED_DEFAULT = True


def templates_enabled() -> bool:
    import os

    raw = os.getenv("WHATSAPP_BOOKING_TEMPLATES_ENABLED", "true" if TEMPLATES_ENABLED_DEFAULT else "false")
    return str(raw).lower() in ("true", "1", "yes")


def template_language() -> str:
    import os

    # User confirmed: all 3 are "English" in dashboard -> API code `en`.
    return (os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "") or TEMPLATE_LANG_DEFAULT).strip() or TEMPLATE_LANG_DEFAULT


def _cloud_creds() -> tuple[str, str, str]:
    phone_id = (getattr(settings, "WHATSAPP_OTP_PHONE_NUMBER_ID", "") or "").strip()
    token = (getattr(settings, "WHATSAPP_OTP_ACCESS_TOKEN", "") or "").strip()
    version = (getattr(settings, "WHATSAPP_OTP_API_VERSION", "") or "v20.0").strip().lstrip("v")
    version = f"v{version}"
    return phone_id, token, version


def _normalize_to(to: str) -> str:
    return re.sub(r"\D", "", to or "")


async def send_cloud_template(
    to: str,
    name: str,
    body_params: Optional[list[str]] = None,
    lang: Optional[str] = None,
) -> dict[str, Any]:
    """POST a template to Cloud API. Never raises — returns success flag.

    Mirrors `app/core/otp_delivery/whatsapp_provider.py:_send_whatsapp_text`
    template branch so auth/payload shape stay identical.
    """
    language = (lang or template_language()).strip() or TEMPLATE_LANG_DEFAULT
    phone_id, token, version = _cloud_creds()
    if not phone_id or not token:
        logger.warning("[booking-template MOCK] to=%s name=%s (WHATSAPP_OTP_* not configured)", to, name)
        return {"success": False, "mock": True, "reason": "missing_whatsapp_cloud_config"}

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    template_obj: dict[str, Any] = {"name": name, "language": {"code": language}}
    if body_params:
        template_obj["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": str(p)[:1024]} for p in body_params]}
        ]
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "template",
        "template": template_obj,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}
                wamid = None
                try:
                    msgs = data.get("messages") if isinstance(data, dict) else None
                    wamid = msgs[0].get("id") if isinstance(msgs, list) and msgs else None
                except Exception:
                    pass
                logger.info("booking-template sent name=%s to=%s*** wamid=%s", name, str(to)[:6], wamid)
                return {"success": True, "wamid": wamid, "response": data}
            body = resp.text[:800]
            logger.error("booking-template failed name=%s status=%s: %s", name, resp.status_code, body)
            return {"success": False, "status_code": resp.status_code, "error": body}
    except Exception as exc:
        logger.error("booking-template error name=%s: %s", name, exc)
        return {"success": False, "error": str(exc)}


async def send_welcome(to: str, name: str, business: str, lang: Optional[str] = None) -> dict[str, Any]:
    return await send_cloud_template(to, TEMPLATE_WELCOME, [name or "there", business or "Dotstark Technologies"], lang)


async def send_request_email(to: str, name: str, lang: Optional[str] = None) -> dict[str, Any]:
    return await send_cloud_template(to, TEMPLATE_REQUEST_EMAIL, [name or "there"], lang)


async def send_request_phone(to: str, lang: Optional[str] = None) -> dict[str, Any]:
    return await send_cloud_template(to, TEMPLATE_REQUEST_PHONE, None, lang)


# ── Lifecycle templates ───────────────────────────────────────────────
# Meta only allows a template for business-initiated messages (reminders,
# out-of-window confirmations). Each must be CREATED and APPROVED in the
# Meta template dashboard with the same body params used here.

async def send_booking_confirmed(
    to: str, name: str, when: str, business: str, lang: Optional[str] = None
) -> dict[str, Any]:
    return await send_cloud_template(
        to,
        TEMPLATE_BOOKING_CONFIRMED,
        [name or "there", when or "your appointment time", business or "Dotstark Technologies"],
        lang,
    )


async def send_booking_reminder(
    to: str, name: str, when: str, business: str, kind: str = "24h", lang: Optional[str] = None
) -> dict[str, Any]:
    tpl = TEMPLATE_BOOKING_REMINDER_24H if kind == "24h" else TEMPLATE_BOOKING_REMINDER_1H
    return await send_cloud_template(
        to, tpl, [name or "there", when or "your appointment time", business or "Dotstark Technologies"], lang
    )


async def send_booking_cancelled(
    to: str, name: str, when: str, business: str, lang: Optional[str] = None
) -> dict[str, Any]:
    return await send_cloud_template(
        to, TEMPLATE_BOOKING_CANCELLED, [name or "there", when or "your appointment", business or "Dotstark Technologies"], lang
    )


async def send_booking_rescheduled(
    to: str, name: str, when: str, business: str, lang: Optional[str] = None
) -> dict[str, Any]:
    return await send_cloud_template(
        to, TEMPLATE_BOOKING_RESCHEDULED, [name or "there", when or "your new time", business or "Dotstark Technologies"], lang
    )


async def send_waitlist_joined(
    to: str, name: str, when: str, business: str, lang: Optional[str] = None
) -> dict[str, Any]:
    return await send_cloud_template(
        to, TEMPLATE_WAITLIST_JOINED, [name or "there", when or "that day", business or "Dotstark Technologies"], lang
    )


async def send_abandoned_booking(
    to: str, name: str, business: str, lang: Optional[str] = None
) -> dict[str, Any]:
    return await send_cloud_template(
        to, TEMPLATE_ABANDONED_BOOKING, [name or "there", business or "Dotstark Technologies"], lang
    )


# ── Per-step resend guard ──────────────────────────────────────────────
# A stuck step (e.g. name never captured) must not re-fire a billable
# Marketing template on every turn. Record one signature per template in
# session state_data; a template resends only when the missing-field set
# changed (i.e. the conversation actually advanced). Pure-dict helpers so
# both transports (and unit tests) share them.

_STEP_TEMPLATES_KEY = "_templates_sent"


def step_signature(template_name: str, missing_fields: Any) -> str:
    try:
        missing = sorted(str(m) for m in (missing_fields or []))
    except Exception:
        missing = []
    return f"{template_name}:{'|'.join(missing)}"


def step_template_already_sent(state_data: Any, template_name: str, missing_fields: Any) -> bool:
    if not isinstance(state_data, dict):
        return False
    sent = state_data.get(_STEP_TEMPLATES_KEY)
    if not isinstance(sent, dict):
        return False
    return sent.get(template_name) == step_signature(template_name, missing_fields)


def mark_step_template_sent(state_data: Any, template_name: str, missing_fields: Any) -> Any:
    base = dict(state_data) if isinstance(state_data, dict) else {}
    sent = dict(base.get(_STEP_TEMPLATES_KEY) or {})
    sent[template_name] = step_signature(template_name, missing_fields)
    return {**base, _STEP_TEMPLATES_KEY: sent}
