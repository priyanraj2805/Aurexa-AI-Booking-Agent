"""Meta Cloud API sender for the booking chat (Cloud transport).

Companion to `whatsapp_templates.py` (template sends) and the Baileys
renderer (`wa-gateway/src/send.js`). This module sends plain-text replies
via `POST /{phone-id}/messages` with `type:text`.

Deliberately NO `type:interactive` here yet — native lists/buttons are a
later phase. Interactive-shaped `wa_msg` dicts (from
`WhatsAppDirectiveRenderer`) are converted to numbered text, mirroring the
gateway's `numberedFallback` so both transports show identical wording.
"""

import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _creds() -> tuple[str, str, str]:
    phone_id = (getattr(settings, "WHATSAPP_OTP_PHONE_NUMBER_ID", "") or "").strip()
    token = (getattr(settings, "WHATSAPP_OTP_ACCESS_TOKEN", "") or "").strip()
    version = (getattr(settings, "WHATSAPP_OTP_API_VERSION", "") or "v20.0").strip().lstrip("v")
    return phone_id, token, f"v{version}"


def wa_msg_to_text(wa_msg: Optional[dict]) -> str:
    """Convert a Baileys-shaped reply dict to Cloud-sendable plain text."""
    if not isinstance(wa_msg, dict):
        return ""
    body = str(wa_msg.get("body") or "").strip()
    options: list[str] = []
    if wa_msg.get("type") == "interactive_buttons":
        options = [str(b.get("title") or "") for b in (wa_msg.get("buttons") or []) if isinstance(b, dict)]
    elif wa_msg.get("type") == "interactive_list":
        for section in (wa_msg.get("sections") or []):
            if not isinstance(section, dict):
                continue
            for row in (section.get("rows") or []):
                if isinstance(row, dict) and str(row.get("title") or ""):
                    options.append(str(row.get("title")))
    if not options:
        return body
    lines = [f"{i + 1}. {title}" for i, title in enumerate(options[:10])]
    numbered = "\n".join(lines)
    suffix = "Reply with the option number."
    return f"{body}\n\n{numbered}\n\n{suffix}" if body else f"{numbered}\n\n{suffix}"


async def send_cloud_text(to: str, body: str) -> dict[str, Any]:
    """Send plain text via Cloud API. Never raises — returns success flag."""
    import re as _re

    digits = _re.sub(r"\D", "", to or "")
    phone_id, token, version = _creds()
    if not phone_id or not token:
        logger.warning("[cloud-chat MOCK] to=%s body_len=%s (WHATSAPP_OTP_* not configured)", to, len(body or ""))
        return {"success": False, "mock": True, "reason": "missing_whatsapp_cloud_config"}
    if not body or not digits:
        return {"success": False, "reason": "empty_body_or_recipient"}
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": digits,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
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
                logger.info("cloud-chat text sent to=%s*** wamid=%s", str(to)[:6], wamid)
                return {"success": True, "wamid": wamid, "response": data}
            err = resp.text[:800]
            logger.error("cloud-chat text failed to=%s*** status=%s: %s", str(to)[:6], resp.status_code, err)
            return {"success": False, "status_code": resp.status_code, "error": err}
    except Exception as exc:
        logger.error("cloud-chat text error to=%s***: %s", str(to)[:6], exc)
        return {"success": False, "error": str(exc)}


async def send_booking_reply(to: str, wa_msg: Optional[dict]) -> dict[str, Any]:
    """Send an engine reply dict over Cloud as text. Never raises."""
    return await send_cloud_text(to, wa_msg_to_text(wa_msg))
