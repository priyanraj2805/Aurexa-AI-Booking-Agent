"""Booking-decline flow — honoring "I don't want to book".

Separate from cancellation (which acts on an EXISTING booking): a decline
means the user does not want a NEW booking right now. The agent answers
with a graceful farewell and stops interrogating for fields, while still
answering questions. Providing real booking data re-engages automatically.

No business content is hardcoded — only generic English decline/re-engage
patterns, mirroring the style of `cancel_flow`.
"""

import logging
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.schemas.agent import ActionPayload, ChatSessionState
from app.crud import chat as crud_chat

logger = logging.getLogger(__name__)

BOOKING_DECLINED_KEY = "_booking_declined"

_NO_BOOKING_PATTERNS = (
    r"\bdon'?t\s+want\s+to\s+bo+k\b",
    r"\bdo\s+not\s+want\s+to\s+bo+k\b",
    r"\bdon'?t\s+want\b.{0,40}\b(?:booking|appointment|reservation|slot|meeting|project)\b",
    r"\bdo\s+not\s+want\b.{0,40}\b(?:booking|appointment|reservation|slot|meeting|project)\b",
    r"\bnot\s+interested\b",
    r"\bno\s+need\s+(?:to\s+book|for\s+(?:a\s+)?(?:booking|appointment))\b",
    r"\bno\s+(?:booking|appointment)\s+for\s+me\b",
    r"\bjust\s+(?:looking|browsing|checking)(?:\s+around)?\b",
    r"\bchanged\s+my\s+mind\b",
    r"\bjust\s+kidding\b",
    r"\bwas\s+just\s+kidding\b",
    r"\bkidding\b.{0,20}\b(?:don'?t\s+want|no\s+booking)\b",
    r"\bi\s+was\s+just\s+kidding\b",
)

_REENGAGE_PATTERNS = (
    r"\blet'?s\s+bo+k\b",
    r"\bactually\b.{0,30}\bbo+k\b",
    r"\bok(?:ay)?\b.{0,20}\bbo+k\b",
    r"\bbo+k\s+(?:it|now|for\s+me|me\s+in|an?\s+(?:appointment|slot|meeting))\b",
    r"\b(?:want|need|i'?d\s+like)\s+to\s+bo+k\b",
    r"\bstart\s+(?:a\s+)?new\s+(?:booking|appointment)\b",
)

# Standalone "no" variants ("Nopee", "nahhh", "no thanks"): the whole message
# is just a refusal with nothing else attached. Longer messages fall through
# to normal refusal/question handling so "no, is parking free?" still works.
# Elongated runs (3+) are squashed first so chat spellings match.
_STANDALONE_NO = frozenset({"no", "nope", "nah", "no thanks", "no thank you"})


def _squash_elongation(text: str) -> str:
    # Collapse ALL letter runs ("Nopeee"->"Nope", "Nahh"->"Nah"). Only used
    # for standalone-token comparison below, never for display or storage.
    return re.sub(r"(.)\1+", r"\1", str(text or ""))


def is_standalone_no(message_text: str) -> bool:
    """True for a bare refusal word and nothing else (see _STANDALONE_NO)."""
    norm = _squash_elongation(str(message_text or "").strip().lower())
    norm = " ".join(norm.split())
    return norm in _STANDALONE_NO


def is_no_booking_request(message_text: str) -> bool:
    """True when the user declines a NEW booking (not a cancellation).

    Cancellation phrasing is excluded here (single choke point) so no
    caller can mistake "don't want to cancel" for a decline — the cancel
    flow owns those turns.
    """
    text = str(message_text or "").strip()
    if not text:
        return False
    try:
        from app.engine.cancel_flow import is_cancel_intent

        if is_cancel_intent(text):
            return False
    except Exception:
        pass
    if is_standalone_no(text):
        return True
    return any(re.search(p, text, re.IGNORECASE) for p in _NO_BOOKING_PATTERNS)


def is_reengage_request(message_text: str) -> bool:
    """True when the user wants to book after all."""
    text = str(message_text or "").strip()
    if not text:
        return False
    return any(re.search(p, text, re.IGNORECASE) for p in _REENGAGE_PATTERNS)


def is_declined(chat_session: ChatSessionState) -> bool:
    try:
        collected = chat_session.collected_data or {}
        return bool(isinstance(collected, dict) and collected.get(BOOKING_DECLINED_KEY))
    except Exception:
        return False


def _business_name(org: Optional[Organization]) -> str:
    try:
        return str(getattr(org, "name", "") or "").strip()
    except Exception:
        return ""


async def start_decline(
    db: AsyncSession,
    org: Optional[Organization],
    chat_session: ChatSessionState,
    message_text: str = "",
    inbound_message_id: Optional[str] = None,
) -> ActionPayload:
    """Record the decline and answer gracefully — no field interrogation."""
    collected = dict(chat_session.collected_data or {})
    collected[BOOKING_DECLINED_KEY] = True
    chat_session.collected_data = collected
    try:
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
    except Exception:
        pass
    # Formal decline — trained via system prompt (prompts.py 9c), not example lists.
    # Keep fallback concise and formal, no service examples.
    msg = (
        "No problem. That's completely okay. If you'd like to book in the future, "
        "feel free to reach out anytime."
    )
    action = ActionPayload(message=msg, widget_directive="TEXT_INPUT", field_meta=None)
    try:
        # Persist the user's ACTUAL words — an empty row destroys the inbox
        # transcript (ghost empty bubble + identical repeat on next turn).
        # The provider id keeps this row traceable to the exact inbound message.
        await crud_chat.save_message(
            db,
            chat_session.session_id,
            "user",
            message_text or "",
            external_message_id=inbound_message_id,
        )
    except Exception:
        pass
    try:
        metadata: Dict[str, Any] = {"directive": "TEXT_INPUT", "intent": "DECLINED_BOOKING"}
        await crud_chat.save_message(
            db, chat_session.session_id, "agent", msg, metadata=metadata
        )
    except Exception:
        pass
    return action


async def clear_decline(
    db: AsyncSession, chat_session: ChatSessionState
) -> bool:
    """Clear a recorded decline (user re-engaged). Returns True if cleared."""
    try:
        collected = dict(chat_session.collected_data or {})
        if not collected.pop(BOOKING_DECLINED_KEY, None) and BOOKING_DECLINED_KEY not in (
            chat_session.collected_data or {}
        ):
            return False
        chat_session.collected_data = collected
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        return True
    except Exception:
        return False


def carries_booking_value(message_text: str) -> bool:
    """True when the message holds a concrete booking value (contact/date).

    Such messages re-engage a declined session without any phrase matching.
    """
    text = str(message_text or "").strip()
    if not text:
        return False
    try:
        from app.engine.cancel_flow import extract_contact_from_text

        if extract_contact_from_text(text) is not None:
            return True
    except Exception:
        pass
    try:
        from app.engine.validators import DataValidator

        iso, err = DataValidator._validate_date(text, "UTC", 365)
        if iso:
            return True
    except Exception:
        pass
    return False


async def maybe_reengage(
    db: AsyncSession,
    chat_session: ChatSessionState,
    message_text: str,
) -> bool:
    """Clear decline when the user re-engages. Returns True if cleared."""
    if not is_declined(chat_session):
        return False
    if is_reengage_request(message_text) or carries_booking_value(message_text):
        return await clear_decline(db, chat_session)
    return False


# ── Field-level refusal ─────────────────────────────────────────────────
# "No" / "don't want to share X" aimed at the CURRENT missing field. Unlike
# a booking decline, the flow cannot advance past a required field — so the
# policy is: acknowledge softly, offer a contact choice when one exists,
# re-ask the same field, and NEVER jump ahead to date/time.

_FIELD_REFUSAL_PATTERNS = (
    r"\bdon'?t\s+want\s+to\s+(?:share|give|provide|tell|send)\b",
    r"\bdo\s+not\s+want\s+to\s+(?:share|give|provide|tell|send)\b",
    r"\bwon'?t\s+(?:share|give|provide)\b",
    r"\bwill\s+not\s+(?:share|give|provide)\b",
    r"\brefuse\s+to\s+(?:share|give|provide)\b",
    r"\bdon'?t\s+ask\b",
    r"\bskip\s+(?:it|this|that)\b",
)

_BARE_NEGATION_RE = re.compile(
    r"^(?:n+o+|nope|nah+|never|not really|dont|don't|no way|nope\.)[.!…]*$",
    flags=re.IGNORECASE,
)

_CONTACT_SIBLING = {"email": "phone", "phone": "email"}


def is_field_refusal(
    message_text: str, missing_fields: Optional[list] = None
) -> bool:
    """True when the message refuses the current missing field.

    Covers explicit refusal ("don't want to share X", "skip it") and bare
    negation ("No", "Nooo", "Nahhh") when a field is actually pending.
    Cancellations, lookups, and questions never match: explicit patterns
    need share/give verbs, and bare negation anchors the whole message.
    """
    text = str(message_text or "").strip()
    if not text or not missing_fields:
        return False
    try:
        from app.engine.cancel_flow import is_cancel_intent

        if is_cancel_intent(text):
            return False
    except Exception:
        pass
    if any(re.search(p, text, re.IGNORECASE) for p in _FIELD_REFUSAL_PATTERNS):
        return True
    return bool(_BARE_NEGATION_RE.match(text))


async def maybe_handle_field_refusal(
    db: AsyncSession,
    org: Optional[Organization],
    chat_session: ChatSessionState,
    message_text: str,
    inbound_message_id: Optional[str] = None,
) -> Optional[ActionPayload]:
    """Soft-handle refusal of the current missing field.

    Acknowledges, offers the sibling contact choice when the refused field
    is email/phone, re-asks the SAME field, and never advances the flow.
    Yields to pending lookups (their contact request owns the turn).
    Returns None when this is not a field refusal.
    """
    try:
        missing = list(chat_session.missing_fields or [])
    except Exception:
        return None
    if not missing or not is_field_refusal(
        message_text, list(chat_session.missing_fields or [])
    ):
        return None
    try:
        collected = chat_session.collected_data or {}
        if isinstance(collected, dict):
            from app.engine.check_booking_flow import CHECK_PENDING_KEY

            if collected.get(CHECK_PENDING_KEY):
                return None
    except Exception:
        pass
    target = missing[0]
    label = str(target).replace("_", " ")
    ack = "No problem."
    extra = ""
    try:
        collected = dict(chat_session.collected_data or {})
        sibling = _CONTACT_SIBLING.get(str(target).lower())
        if sibling and not str(collected.get(sibling) or "").strip():
            sibling_label = sibling.replace("_", " ")
            extra = (
                f" I can use either your {label} or your {sibling_label} — "
                "whichever you're comfortable sharing. "
            )
    except Exception:
        pass
    msg = (
        f"{ack}{extra} When you're ready, I'll need your {label} to continue — "
        "or just say 'stop' anytime and we'll end here."
    )
    action = ActionPayload(message=msg, widget_directive="TEXT_INPUT", field_meta=None)
    try:
        await crud_chat.save_message(
            db,
            chat_session.session_id,
            "user",
            message_text,
            external_message_id=inbound_message_id,
        )
    except Exception:
        pass
    try:
        metadata: Dict[str, Any] = {
            "directive": "TEXT_INPUT",
            "intent": "FIELD_REFUSAL",
        }
        await crud_chat.save_message(
            db, chat_session.session_id, "agent", msg, metadata=metadata
        )
    except Exception:
        pass
    return action
