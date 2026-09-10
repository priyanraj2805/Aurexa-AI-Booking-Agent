"""
WhatsApp reply formatter (transport-agnostic).

Converts engine ActionPayloads into plain reply dicts consumed by whichever
transport delivers them (Baileys gateway via the internal QR inbound route).
The old Meta Cloud API send functions were removed with the Cloud transport;
see wa-gateway/src/send.js for Baileys rendering.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


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
