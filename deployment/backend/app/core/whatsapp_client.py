"""
WhatsApp Cloud API client for sending outbound messages.

Uses Meta's Graph API v17.0:
https://graph.facebook.com/v17.0/{phone_number_id}/messages
"""

import logging
import httpx
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v17.0"


async def send_typing_indicator(
    phone_number_id: str,
    api_token: str,
    to_phone: str,
    is_typing: bool = True,
) -> dict:
    """
    Send a typing indicator to show the user that the bot is composing a response.
    WhatsApp Cloud API supports "typing_on" and "typing_off" signals.
    """
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "typing_signal",
        "typing_signal": {"type": "typing_on" if is_typing else "typing_off"},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"Failed to send typing indicator: {e}")
        return {}


async def send_text_message(
    phone_number_id: str,
    api_token: str,
    to_phone: str,
    text: str,
) -> dict:
    """Send a plain text message via WhatsApp Cloud API."""
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def send_interactive_buttons(
    phone_number_id: str,
    api_token: str,
    to_phone: str,
    body_text: str,
    buttons: List[Dict[str, str]],
) -> dict:
    """
    Send an interactive button message (max 3 buttons).
    Each button: {"id": "btn_confirm", "title": "Confirm"}
    """
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    button_rows = [
        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
        for b in buttons[:3]
    ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": button_rows},
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def send_interactive_list(
    phone_number_id: str,
    api_token: str,
    to_phone: str,
    body_text: str,
    button_text: str,
    sections: List[Dict],
) -> dict:
    """
    Send an interactive list message for selections (e.g., time slots).
    sections: [{"title": "Available Times", "rows": [{"id": "...", "title": "...", "description": "..."}]}]
    """
    url = f"{GRAPH_API_BASE}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_text[:20],
                "sections": sections,
            },
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def format_action_for_whatsapp(action_payload) -> dict:
    """
    Convert an ActionPayload into a WhatsApp-friendly message dict.
    Returns: {"type": "text"|"interactive_buttons"|"interactive_list", "body": ..., "buttons": ..., ...}
    """
    directive = action_payload.widget_directive or "TEXT_INPUT"
    message = action_payload.message or ""
    # Strip markdown bold (WhatsApp uses *bold* natively)
    message = message.replace("**", "*")

    # Summary / confirmation cards → interactive buttons
    if directive in ("REQUEST_CONFIRMATION", "BOOKING_SUMMARY_CARD"):
        return {
            "type": "interactive_buttons",
            "body": message,
            "buttons": [
                {"id": "btn_confirm", "title": "✅ Confirm"},
                {"id": "btn_edit", "title": "✏️ Edit"},
                {"id": "btn_cancel", "title": "❌ Cancel"},
            ],
        }

    # OTP prompt → plain text (WhatsApp can't show modals)
    if directive == "REQUIRE_OTP_MODAL":
        return {
            "type": "text",
            "body": message + "\n\nReply with your verification code.",
        }

    # Booking confirmed → plain text with emoji
    if directive == "BOOKING_CONFIRMED_CARD":
        return {"type": "text", "body": message}

    # Select dropdown → interactive list or buttons
    if directive == "SELECT_DROPDOWN" and action_payload.field_meta:
        options = action_payload.field_meta.get("options", [])
        if 1 <= len(options) <= 3:
            return {
                "type": "interactive_buttons",
                "body": message,
                "buttons": [
                    {"id": f"opt_{i}", "title": o[:20]} for i, o in enumerate(options)
                ],
            }
        elif len(options) > 3:
            rows = [
                {"id": f"opt_{i}", "title": o[:24]} for i, o in enumerate(options[:10])
            ]
            return {
                "type": "interactive_list",
                "body": message,
                "button_text": "Choose",
                "sections": [{"title": "Options", "rows": rows}],
            }

    # Date/time pickers → plain text (WhatsApp has no native pickers)
    if directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"):
        return {"type": "text", "body": message}

    # Default: plain text
    return {"type": "text", "body": message}
