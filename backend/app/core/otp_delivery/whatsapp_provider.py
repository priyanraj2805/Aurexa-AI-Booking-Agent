"""WhatsApp OTP provider — Meta Cloud API, plain text (no template needed for test numbers).

Uses the same httpx pattern as app/core/whatsapp_client.py.
For production, swap text body to template with {{1}} -> no code change beyond env.
"""
import logging
import re
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

def _normalize_whatsapp_to(to: str) -> str:
    # Meta expects digits without '+', spaces, dashes, e.g. 919876543210
    digits = re.sub(r"\D", "", to or "")
    return digits

async def _send_whatsapp_text(to: str, text: str) -> dict:
    phone_id = (getattr(settings, "WHATSAPP_OTP_PHONE_NUMBER_ID", "") or "").strip()
    token = (getattr(settings, "WHATSAPP_OTP_ACCESS_TOKEN", "") or "").strip()
    version = (getattr(settings, "WHATSAPP_OTP_API_VERSION", "") or "v20.0").strip().lstrip("v")
    version = f"v{version}" if not version.startswith("v") else version

    if not phone_id or not token:
        logger.warning(f"[WHATSAPP OTP MOCK] to={to} (WHATSAPP_OTP_* not configured)")
        return {"success": False, "mock": True, "status": "mock", "reason": "missing_whatsapp_otp_config"}

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    # Plain text for test numbers — works within test recipient list without template approval
    # For production template: {"type":"template","template":{"name": "...","language":{"code":"en_US"},"components":[{"type":"body","parameters":[{"type":"text","text": code}]}]}}
    template = (getattr(settings, "WHATSAPP_OTP_TEMPLATE_NAME", "") or "").strip()
    if template:
        lang = (getattr(settings, "WHATSAPP_OTP_LANGUAGE", "") or "en_US").strip()
        import re as _re
        m = _re.search(r"\b(\d{6})\b", text)
        code = m.group(1) if m else text
        # aurexa_otp is an Authentication template whose button at index 0 is a
        # URL button with a {{1}} variable (verified via API errors 132018/131008).
        # The code is sent as the body parameter and as the URL suffix parameter.
        if template == "aurexa_otp":
            payload = {
                "messaging_product": "whatsapp",
                "to": _normalize_whatsapp_to(to),
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": lang},
                    "components": [
                        {"type": "body", "parameters": [{"type": "text", "text": code}]},
                        {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": code}]},
                    ],
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": _normalize_whatsapp_to(to),
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": lang},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": code}]}],
                },
            }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": _normalize_whatsapp_to(to),
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json() if resp.headers.get("content-type","").startswith("application/json") else {"raw": resp.text}
                wamid = None
                try:
                    wamid = data.get("messages", [{}])[0].get("id") if isinstance(data.get("messages"), list) else None
                except Exception:
                    pass
                logger.info(f"WhatsApp OTP sent to {to[:6]}*** wamid={wamid} status={resp.status_code}")
                return {"success": True, "wamid": wamid, "response": data}
            body = resp.text[:800]
            logger.error(f"WhatsApp OTP failed to {to} {resp.status_code}: {body}")
            if resp.status_code in (400, 401, 403, 404):
                return {"success": False, "status_code": resp.status_code, "error": body}
            resp.raise_for_status()
            return {"success": False, "error": body}
    except Exception as e:
        logger.error(f"WhatsApp OTP failed to {to}: {e}")
        raise

class WhatsAppProvider:
    async def send_otp(self, to: str, code: str, session_id: str) -> dict:
        text = f"Your DotStark verification code is {code}. It expires in 5 minutes. Do not share this code."
        return await _send_whatsapp_text(to, text)
