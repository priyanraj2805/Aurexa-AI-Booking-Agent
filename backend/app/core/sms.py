"""Plivo SMS service for phone OTP."""
import logging
import anyio
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sms_sync(to: str, text: str) -> dict:
    """Synchronous Plivo SMS sender (runs in worker thread)."""
    # Mock mode: no creds or DEBUG -> log and pretend success (like mail.py DEBUG path)
    if settings.DEBUG or not settings.PLIVO_AUTH_ID or not settings.PLIVO_AUTH_TOKEN or not settings.PLIVO_SENDER_ID:
        logger.warning(f"[SMS MOCK] to={to} text={text!r} (PLIVO not configured or DEBUG=true)")
        return {"success": True, "mock": True}

    try:
        import plivo

        client = plivo.RestClient(settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN)
        # Plivo expects E.164 dst, src is your Plivo number / sender ID
        resp = client.messages.create(
            src=settings.PLIVO_SENDER_ID,
            dst=to,
            text=text,
        )
        logger.info(f"Plivo SMS sent to {to} uuid={getattr(resp, 'message_uuid', resp)}")
        return {"success": True, "message_uuid": getattr(resp, "message_uuid", None)}
    except Exception as e:
        logger.error(f"Plivo SMS failed to {to}: {e}")
        raise


async def send_phone_otp_sms(to: str, code: str) -> dict:
    """Send a 6-digit phone OTP via Plivo SMS."""
    text = f"Your DotStark verification code is {code}. It expires in 5 minutes. Do not share this code."
    return await anyio.to_thread.run_sync(_send_sms_sync, to, text, abandon_on_cancel=True)


async def send_sms(to: str, text: str) -> dict:
    """Generic SMS sender."""
    return await anyio.to_thread.run_sync(_send_sms_sync, to, text, abandon_on_cancel=True)
