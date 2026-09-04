"""Zavu SMS service for phone OTP (kd78m7agx48rpyegpfrmhp5rsx8dm8t9 sms_oneway)."""
import logging
import anyio
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_zavu_sms_sync(to: str, text: str) -> dict:
    """Synchronous Zavu SMS sender via /v1/messages channel=sms."""
    api_key = (getattr(settings, "ZAVU_API_KEY", "") or "").strip()
    sender_id = (getattr(settings, "ZAVU_SENDER_ID", "") or "kd78m7agx48rpyegpfrmhp5rsx8dm8t9").strip()
    base_url = (getattr(settings, "ZAVU_BASE_URL", "") or "https://api.zavu.dev").rstrip("/")
    if not api_key:
        logger.warning(f"[ZAVU MOCK] to={to} text={text!r} (ZAVU_API_KEY missing)")
        return {"success": True, "mock": True, "reason": "missing_api_key"}
    try:
        import httpx

        url = f"{base_url}/v1/messages"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if sender_id:
            headers["Zavu-Sender"] = sender_id
        # Sender kd78... has channels ["sms_oneway"] (shared pool) — use sms_oneway, not sms which needs a dedicated number
        payload = {"to": to, "text": text, "channel": "sms_oneway"}
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            # Zavu returns 202 on accepted (queued)
            if resp.status_code in (200, 201, 202):
                data = {}
                try:
                    data = resp.json()
                except Exception:
                    data = {"raw": resp.text}
                msg_id = None
                if isinstance(data, dict):
                    msg = data.get("message") if isinstance(data.get("message"), dict) else data
                    msg_id = msg.get("id") or msg.get("message_id") if isinstance(msg, dict) else None
                logger.info(f"Zavu SMS sent to {to} id={msg_id} status={resp.status_code}")
                return {"success": True, "message_id": msg_id, "status_code": resp.status_code, "response": data}
            # Map known Zavu error codes
            body = resp.text[:800]
            # Test keys (zv_test_*) sandbox only supports WhatsApp — treat SMS sandbox error as mock success for local dev
            if api_key.startswith("zv_test_") and "Sandbox mode only supports WhatsApp" in body:
                logger.warning(f"[ZAVU MOCK] test key sandbox SMS not delivered to {to} — treating as mock success (use zv_live_* for real delivery)")
                return {"success": True, "mock": True, "reason": "test_key_sandbox_whatsapp_only", "status_code": resp.status_code}
            logger.error(f"Zavu SMS failed to {to} {resp.status_code}: {body}")
            # Do not raise for test key simulation errors like destination_not_verified — surface as failure dict
            if resp.status_code in (400, 402, 403, 404, 429):
                return {"success": False, "status_code": resp.status_code, "error": body, "mock": False}
            resp.raise_for_status()
            return {"success": False, "status_code": resp.status_code, "error": body}
    except Exception as e:
        logger.error(f"Zavu SMS failed to {to}: {e}")
        raise


def _send_sms_sync(to: str, text: str) -> dict:
    """Synchronous SMS sender — Zavu primary, Plivo fallback if Zavu not configured."""
    # Prefer Zavu if configured (even in DEBUG we use Zavu mock path which logs)
    if getattr(settings, "ZAVU_API_KEY", ""):
        return _send_zavu_sms_sync(to, text)
    # Legacy Plivo fallback (kept for backward compat, remove after Zavu verified)
    plivo_id = getattr(settings, "PLIVO_AUTH_ID", None)
    plivo_token = getattr(settings, "PLIVO_AUTH_TOKEN", None)
    plivo_src = getattr(settings, "PLIVO_SENDER_ID", None)
    if plivo_id and plivo_token and plivo_src:
        try:
            import plivo

            client = plivo.RestClient(plivo_id, plivo_token)
            resp = client.messages.create(src=plivo_src, dst=to, text=text)
            logger.info(f"Plivo SMS sent to {to} uuid={getattr(resp, 'message_uuid', resp)}")
            return {"success": True, "message_uuid": getattr(resp, "message_uuid", None)}
        except Exception as e:
            logger.error(f"Plivo SMS failed to {to}: {e}")
            raise
    logger.warning(f"[SMS MOCK] to={to} text={text!r} (ZAVU_API_KEY and PLIVO not configured)")
    return {"success": True, "mock": True}


async def send_phone_otp_sms(to: str, code: str) -> dict:
    """Send a 6-digit phone OTP via Plivo SMS."""
    text = f"Your DotStark verification code is {code}. It expires in 5 minutes. Do not share this code."
    return await anyio.to_thread.run_sync(_send_sms_sync, to, text, abandon_on_cancel=True)


async def send_sms(to: str, text: str) -> dict:
    """Generic SMS sender."""
    return await anyio.to_thread.run_sync(_send_sms_sync, to, text, abandon_on_cancel=True)
