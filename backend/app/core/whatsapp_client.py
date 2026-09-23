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


def attach_knowledge_answer(action_message: str, directive_body: str) -> str:
    """Prepend a knowledge answer above a pending-step render.

    Mid-booking questions ("tell me about X") are answered by the engine
    while the booking step stays pending. Without this, the step render
    replaces the answer and the customer never sees it. Returns the
    directive body unchanged when there is no substantive answer.
    """
    answer = str(action_message or "").strip().replace("**", "*")
    body = str(directive_body or "").strip()
    if not answer or not body:
        return body or answer
    if answer.lower() in body.lower() or body.lower() in answer.lower():
        return body
    return f"{answer}\n\n{body}"[:4096]


def format_action_for_whatsapp(action_payload) -> dict:
    """
    Convert an ActionPayload into a WhatsApp-friendly message dict.
    Returns: {"type": "text"|"interactive_buttons"|"interactive_list", "body": ..., "buttons": ..., ...}
    """
    directive = action_payload.widget_directive or "TEXT_INPUT"
    message = action_payload.message or ""
    # Strip markdown bold (WhatsApp uses *bold* natively)
    message = message.replace("**", "*")
    # Human-texting style guarantee: em/en dashes read as AI-generated.
    # Spaced em dashes become commas, bare ones get spaces trimmed into
    # commas, and en dashes (often ranges) become plain hyphens.
    message = message.replace(" — ", ", ").replace("—", ",").replace("–", "-")

    # Validator corrections must be spoken, not just attached. The engine
    # composes its text BEFORE validation runs, and WhatsApp has no inline
    # validation UI (unlike the web widget) — so a rejected value's precise
    # reason (e.g. "Invalid email: ...") is prepended here when the body
    # doesn't already carry it. Without this, users get vague prose and no
    # correction after typing a wrong email/phone/name.
    try:
        field_meta = getattr(action_payload, "field_meta", None) or {}
        correction = str(field_meta.get("validation_error") or "").strip()
        if correction and correction.replace("**", "*").lower() not in message.lower():
            message = f"{correction}\n\n{message}" if message else correction
            message = message.replace("**", "*")
            message = message.replace(" — ", ", ").replace("—", ",").replace("–", "-")
    except Exception:
        pass

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

    # Select dropdown / select field → interactive list or buttons
    # Chat widget uses REQUIRE_SELECT for custom select fields, SELECT_DROPDOWN is legacy
    if directive in ("SELECT_DROPDOWN", "REQUIRE_SELECT") and action_payload.field_meta:
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

    # Date picker / time slots → interactive list when options exist.
    # WhatsApp has no native pickers, but tappable rows beat free text — and
    # taps arrive as plain text that parses through the normal validators.
    # Without options, fall back to plain text (unchanged behavior).
    if directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"):
        try:
            options = [
                str(o).strip()
                for o in ((action_payload.field_meta or {}).get("options") or [])
                if str(o).strip()
            ]
        except Exception:
            options = []
        if options:
            shown, extra = options[:10], max(0, len(options) - 10)
            body = message
            if extra:
                body = f"{message}\n\n…and {extra} more — reply with your preferred time."
            return {
                "type": "interactive_list",
                "body": body,
                "button_text": "Choose date"
                if directive == "REQUIRE_DATE_PICKER"
                else "Choose time",
                "sections": [
                    {
                        "title": "Upcoming days"
                        if directive == "REQUIRE_DATE_PICKER"
                        else "Available times",
                        "rows": [
                            {"id": o[:64], "title": o[:24]} for o in shown
                        ],
                    }
                ],
            }
        return {"type": "text", "body": message}

    # Default: plain text
    return {"type": "text", "body": message}
