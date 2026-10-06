"""
WhatsApp reply formatter (transport-agnostic).

Converts engine ActionPayloads into plain reply dicts consumed by whichever
transport delivers them (Baileys gateway via the internal QR inbound route).
The old Meta Cloud API send functions were removed with the Cloud transport;
see wa-gateway/src/send.js for Baileys rendering.
"""

import logging
import re
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
    # Strip a booking push from the answer first, using the same patterns as
    # the engine. Production: the engine's message was ONLY the push
    # ("Is there anything I can help you book, check or reschedule?"), so
    # combining it with the decline (which ends in the same sentence) sent
    # the call-to-action twice in one message. An answer with no content
    # beyond the push is dropped and the step body goes out alone.
    try:
        from app.engine.user_visible_text import (
            _BOOKING_REDIRECT_SENTENCE_RE as _push_re,
            strip_booking_redirect as _strip_push,
        )

        answer = _strip_push(answer)
        content_only = _push_re.sub("", answer)
        content_only = re.sub(r"[ \t]{2,}", " ", content_only).strip()
        if len(content_only) < 20:
            return body
    except Exception:
        pass
    if answer.lower() in body.lower() or body.lower() in answer.lower():
        return body
    return f"{answer}\n\n{body}"[:4096]


async def break_identical_repeat(db, chat_session, body: str) -> str:
    """Repeat tracker for constant-answer loops (observe-only).

    Tracks the last reply body and consecutive-repeat count in session
    state so stuck loops stay visible in diagnostics, but NEVER mutates
    the customer-facing text: appended HUMAN/nudge lines reached users on
    stuck turns and read as nagging, so all mutation was removed. Stuck
    loops are fixed in the engine itself (validation guards, context
    memory, cooldown rescue), never by editing replies here. Fail-open:
    any error returns the body untouched.
    """
    try:
        if not (body or "").strip():
            return body
        from app.crud import chat as crud_chat

        data = dict(getattr(chat_session, "collected_data", None) or {})
        last = str(data.get("_last_reply_body") or "")
        if (body or "").strip() == last.strip() and last.strip():
            data["_repeat_count"] = int(data.get("_repeat_count") or 0) + 1
        else:
            data["_last_reply_body"] = body
            data["_repeat_count"] = 0
        chat_session.collected_data = data
        try:
            await crud_chat.update_chat_session(
                db, str(chat_session.session_id), chat_session)
        except Exception:
            pass
        return body
    except Exception:
        return body


def format_action_for_whatsapp_text_only(action_payload) -> dict:
    """Always plain text, never interactive elements.

    WhatsApp booking is a conversation: the customer types a date, a time and
    "yes", and the agent reads it back. Tappable lists and buttons were never
    wanted on this channel, and relying on the BookingDirective block to
    suppress them was fragile — with no directive row the caller fell through
    to `format_action_for_whatsapp`, which returns an interactive list
    whenever the field carries options. This function is the WhatsApp
    transport's terminal formatter, so interactive output is impossible
    regardless of upstream state.

    Options are rendered inline as text so the customer can still see what
    they may choose. The web widget keeps using `format_action_for_whatsapp`.
    """
    directive = action_payload.widget_directive or "TEXT_INPUT"
    field_meta = getattr(action_payload, "field_meta", None) or {}
    options = [
        str(o).strip()
        for o in (field_meta.get("options") or [])
        if isinstance(o, str) and str(o).strip()
    ]
    if not options:
        options = [
            str(o.get("label") or o.get("value") or "").strip()
            for o in (field_meta.get("options") or [])
            if isinstance(o, dict) and str(o.get("label") or o.get("value") or "").strip()
        ]
    prepared = format_action_for_whatsapp(action_payload)
    body = str(prepared.get("body") or "")

    # Time slots are listed: the customer has to see them to reply with one.
    #
    # Dates are NOT listed. The date step states the booking window in words
    # (see the WhatsApp date step in the orchestrator) because a list of day
    # names reads as a menu, and any day on it can still turn out to be full
    # -- the customer picks from the menu and is then told the day is
    # unavailable. The horizon and the closed-day note are what they need.
    if options and directive == "REQUIRE_TIME_SLOT_GRID":
        line = "Available times: " + " · ".join(options[:10])
        extra = len(options) - 10
        if extra > 0:
            line += f"\n…and {extra} more."
        if "available times" not in body.lower():
            body = f"{body}\n\n{line}".strip()

    # Every directive goes out verbatim: the engine message already tells
    # the customer what to do, and anything appended here reaches the
    # customer but never the admin transcript.
    return {"type": "text", "body": body}


def format_action_for_whatsapp(action_payload) -> dict:
    """
    Convert an ActionPayload into a WhatsApp-friendly message dict.
    Returns: {"type": "text"|"interactive_buttons"|"interactive_list", "body": ..., "buttons": ..., ...}
    """
    directive = action_payload.widget_directive or "TEXT_INPUT"
    message = action_payload.message or ""
    # Central outbound formatting. Every reply — graph, legacy, cancel flow,
    # decline flow, directive acknowledgements — is composed in a different
    # place with its own layout, so formatting is applied once here instead of
    # being re-implemented per call site. Handles WhatsApp single-asterisk
    # bold, paragraph spacing, bullet normalisation, and strips internal
    # template headers (e.g. "BUSINESS HOURS") that leaked to customers.
    try:
        from app.core.wa_text import format_outbound

        message = format_outbound(message)
    except Exception:
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
        # Normalise whitespace before the containment check: the body is
        # line-wrapped for WhatsApp ("future\ndate") while the correction
        # carries spaces ("future date") — without this the same sentence
        # renders twice.
        import re as _re_ws

        def _norm(s: str) -> str:
            return _re_ws.sub(r"\s+", " ", str(s or "").replace("**", "*")).strip().lower()

        if correction and _norm(correction) not in _norm(message):
            message = f"{correction}\n\n{message}" if message else correction
            # Re-format: the correction is prepended after the first pass, so
            # it must go through the same WhatsApp formatting rules.
            try:
                from app.core.wa_text import format_outbound

                message = format_outbound(message)
            except Exception:
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
