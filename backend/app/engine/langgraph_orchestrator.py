import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from langchain_core.messages import HumanMessage, AIMessage

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.booking import BusinessSchedule
from app.schemas.agent import ChatSessionState, ActionPayload
from app.crud import chat as crud_chat
from app.crud import otp as otp_crud
from app.core import mail as mailer
from app.engine.checkpoint_manager import CheckpointManager
from app.engine.agent.graph import booking_agent
from app.engine.evaluator import StateEvaluator
from app.engine import message_processor
from app.engine import cancel_flow
from app.engine import check_booking_flow
from app.engine import decline_flow
from app.engine.recipient_resolution import resolve_email_from_collected_data
from app.engine.user_visible_text import (
    extract_user_visible_text,
    looks_like_confirmation_claim,
    looks_like_field_saved_claim,
    looks_like_json_debris,
)
from app.schemas.agent import FieldDefinition, parse_field_definitions, order_field_definitions

logger = logging.getLogger(__name__)
MAX_RUNTIME_CONTEXT_MESSAGES = 6
OTP_COOLDOWN_SECONDS = 30
OTP_RESEND_CAP = 3
OTP_VALID_FOR_SECONDS = 300
GRAPH_TIMEOUT_SECONDS = 60


async def _whatsapp_hours_prefix(db: AsyncSession, org: Organization) -> str:
    try:
        from app.engine.scheduling_policy import load_weekly_hours_block

        _full = await load_weekly_hours_block(db, str(org.id))
        if not _full or "Monday" not in _full:
            return ""
        # First CONTENT line, never a section header. When the block opened
        # with "## BUSINESS HOURS", stripping the markers left the bare header,
        # which was then prepended to the customer message verbatim.
        # Production: "The capital of Japan is Tokyo. BUSINESS HOURS Thanks!
        # Could you please share your preferred date?"
        for _raw_line in _full.split("\n"):
            _candidate = _raw_line.replace("## ", "").replace(
                " (authoritative — answer timing questions from this)", ""
            ).strip()
            if not _candidate or len(_candidate) >= 120:
                continue
            if _candidate.upper() in (
                "BUSINESS HOURS", "OUR SERVICES", "OPENING HOURS",
                "WORKING HOURS", "HOURS",
            ):
                continue
            return _candidate
        return ""
    except Exception:
        logger.warning("whatsapp hours prefix failed", exc_info=True)
        return ""


def _slot_policy_kwargs(org_config) -> dict:
    return {
        "allow_same_day_booking": bool(getattr(org_config, "allow_same_day_booking", False)),
        "slot_cutoff_minutes": getattr(org_config, "slot_cutoff_minutes", 5),
    }


async def _has_available_persisted_slot(
    db: AsyncSession,
    org_id: Any,
    target_date: Any,
    requested_time: Any,
    timezone_name: str,
    allowed_slot_id: Optional[str] = None,
) -> bool:
    try:
        from datetime import datetime, timezone
        from dateutil.parser import parse as date_parse
        from app.models.booking import TimeSlot
        from zoneinfo import ZoneInfo

        parsed_date = date_parse(str(target_date)).date()
        parsed_time = (
            date_parse(str(requested_time)).time()
            if requested_time
            else None
        )
        try:
            business_zone = ZoneInfo(str(timezone_name or "UTC"))
        except Exception:
            business_zone = ZoneInfo("UTC")
        start_utc = (
            datetime.combine(
                parsed_date, parsed_time, tzinfo=business_zone
            ).astimezone(timezone.utc)
            if parsed_time
            else None
        )
        if allowed_slot_id and not requested_time:
            try:
                import uuid as _uuid_slot

                slot_id = _uuid_slot.UUID(str(allowed_slot_id))
                locked_result = await db.execute(
                    select(TimeSlot).where(
                        TimeSlot.id == slot_id,
                        TimeSlot.org_id == org_id,
                        TimeSlot.is_booked.is_(False),
                    )
                )
                locked_slot = locked_result.scalars().first()
                if locked_slot is not None:
                    locked_start = locked_slot.start_time
                    if locked_start.tzinfo is None:
                        locked_start = locked_start.replace(tzinfo=timezone.utc)
                    if locked_start.astimezone(business_zone).date() == parsed_date:
                        return True
            except Exception:
                pass
        if not requested_time:
            return False
        result = await db.execute(
            select(TimeSlot).where(
                TimeSlot.org_id == org_id,
                TimeSlot.start_time == start_utc,
                TimeSlot.is_booked.is_(False),
            )
        )
        slot = result.scalars().first()
        if slot is None:
            return False
        if slot.locked_until:
            locked_until = slot.locked_until
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if locked_until > datetime.now(timezone.utc):
                if not allowed_slot_id or str(slot.id) != str(allowed_slot_id):
                    return False
        return True
    except Exception:
        return False


def _extract_user_visible_message(raw_message: Any) -> tuple[str, Dict[str, Any]]:
    return extract_user_visible_text(raw_message)


async def _persist_transcript_best_effort(
    db: AsyncSession,
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
    directive: str,
    intent: Optional[str],
    user_external_id: Optional[str] = None,
) -> None:
    if not hasattr(db, "add") or not hasattr(db, "commit"):
        return
    try:
        await crud_chat.save_message(
            db, session_id, "user", user_message, external_message_id=user_external_id
        )
        metadata: Dict[str, Any] = {"directive": directive}
        if intent:
            metadata["intent"] = intent
        await crud_chat.save_message(
            db, session_id, "agent", assistant_message, metadata=metadata
        )
    except Exception as exc:  # pragma: no cover - best effort parity with legacy path
        logger.warning("Failed to persist graph transcript: %s", exc)


# Session states a booking can never return from. Once one of these is on
# disk, a later turn holding a stale in-memory copy must not write a
# non-terminal status over it, or the customer is pushed into booking again.
_TERMINAL_SESSION_STATUSES = frozenset({"CONFIRMED", "CANCELLED", "EXPIRED"})


# Signs that the model is declining rather than answering from the knowledge
# base. Any of these means "we don't have this", whatever words it used.
_OFFTOPIC_DECLINE_RE = re.compile(
    # A decline must OPEN the reply, or open a clause after a real sentence.
    # Matching anywhere discarded real answers: in "We do Kentico
    # development. I do not have information about timelines." an unanchored
    # pattern fired on the trailing caveat and the Kentico fact was thrown
    # away. Variable-length lookbehind is not available in `re`, so the
    # boundary is matched as a prefix and stripped by the caller.
    r"(?:^|(?<=[.!?])\s+|(?<=,)\s+|\bbut\s+|\bhowever,?\s+)"
    r"(?:"
    r"(?:i\s+)?(?:don'?t|do\s+not|can'?t|cannot|couldn'?t|won'?t)\s+"
    r"(?:have|know|find|be\s+able|answer|help|provide|contain)"
    r"|(?:i\s+)?(?:am|'m)\s+(?:just\s+)?a\s+booking\s+assistant"
    r"|there\s+(?:is|are)\s+no\s+information"
    r"|no\s+(?:information|details?|record|data)\s+(?:about|on|of|for)\s+that"
    r"|(?:this|that|it|this\s+information)\s+is\s+not\s+"
    r"|it\s+isn'?t\s+(?:in|covered|listed|available|part\s+of|mentioned)"
    r"|this\s+isn'?t\s+(?:in|covered|listed|available|mentioned)"
    r"|that\s+isn'?t\s+(?:in|covered|listed|available|mentioned)"
    r"|(?:not|isn'?t|aren'?t)\s+listed"
    r"|unable\s+to\s+(?:find|answer|help|provide)\s+that"
    r"|outside\s+(?:my|our|the)\s+(?:scope|remit)"
    r"|knowledge\s+(?:materials|base|documents)\s+don'?t"
    r"|not\s+something\s+i\s+(?:can|have|know)"
    r")",
    flags=re.IGNORECASE,
)

_OFFTOPIC_REPLY = (
    "I'm a booking assistant, so I can only help with appointments — "
    "I don't have information about that.\n\n"
    "Is there anything I can help you book, check or reschedule?"
)

# How much real content must appear before a decline marker for the reply to
# count as a PARTIAL answer. Long enough that a throwaway clause like "I don't
# have that" never qualifies, short enough that one useful sentence does.
_MIN_ANSWER_CHARS = 40

# Phrases that open a refusal. Kept as plain substrings and applied to the
# WHOLE reply, never per-clause: trying to locate "the refusal" inside a
# partially correct answer was exactly the bug that deleted real content.
_REFUSAL_OPENERS = (
    "i don't have", "i do not have", "i cannot", "i can't", "i couldn't",
    "i'm unable", "i am unable", "i can't find", "i cannot find",
    "i couldn't find", "i don't know", "i do not know",
    "i'm just a booking assistant", "i am just a booking assistant",
    "i'm a booking assistant", "i am a booking assistant",
    "there is no information", "there are no details",
    "this isn't", "this is not", "that isn't covered", "that's not covered",
    "unfortunately", "regrettably",
    "knowledge materials don't", "knowledgebase doesn't",
    "knowledge base doesn't", "the materials don't", "the materials do not",
    "the documents don't", "the documents do not",
    "that's outside", "that is outside",
    # Subject-first refusals: "That is not available in our documents.",
    # "The company knowledge materials don't include...". The model uses
    # these often enough that omitting them let the cold wording through.
    "that is not", "that's not", "that is unavailable", "that is not available",
    "that isn't", "these materials don't", "these materials do not",
    "this material doesn't", "our knowledge materials", "the company knowledge",
    # The model very often prefixes the refusal with a courtesy word, which
    # made "Sorry, I don't have information about this." read as an answer and
    # reach the customer verbatim.
    "sorry,", "sorry!", "unfortunately,", "unfortunately.",
    "i'm sorry", "i am sorry", "afraid not",
)

# Courtesy prefixes that are not themselves part of the refusal, so the
# remaining text can be judged on its own content.
_COURTESY_PREFIX_RE = re.compile(
    r"^(?:sorry|unfortunately|afraid\s+not|regrettably)[,!.\s]+",
    flags=re.IGNORECASE,
)

# A booking redirect glued onto the end of an answer. Production symptom: a
# customer asked what the capital of Japan is and got
#   "The capital of Japan is Tokyo. Back to your booking, could you please
#    share a preferred date?"
# The answer arrived, and was immediately buried under a push. Asking one
# question should not read as an attempt to resume the booking. The step is
# still re-asked on the next turn, which is enough.
_BOOKING_REDIRECT_SENTENCE_RE = re.compile(
    r"(?:(?<=^)|(?<=[.!?]))\s*"
    r"(?:"
    r"(?:back|getting)\s+to\s+(?:your|the)\s+booking"
    r"|for\s+your\s+booking"
    r"|(?:continue|proceed|resume)\s+(?:with\s+)?(?:the\s+|your\s+)?booking"
    r"|(?:would|do)\s+you\s+like\s+to\s+(?:book|schedule|continue|proceed)"
    r"|(?:let'?s|shall\s+we)\s+(?:get|set)\s+(?:your\s+\w+\s+)?(?:set\s+up|going|booked)"
    r"|which\s+date\s+works\s+for\s+you"
    r"|do\s+you\s+want\s+to\s+(?:book|schedule|proceed)"
    r"|(?:could|can|would)\s+you\s+please\s+share\s+(?:your\s+)?"
    r"(?:preferred\s+|booking\s+|appointment\s+)?(?:date|time)"
    r"|what\s+date\s+would\s+you\s+(?:like|prefer)"
    r"|please\s+(?:share|provide)\s+your\s+(?:preferred\s+)?(?:date|time)"
    r"|please\s+choose\s+your\s+preferred\s+date"
    r"|please\s+share\s+a\s+preferred\s+(?:date|time)"
    r"|share\s+your\s+(?:preferred\s+)?(?:date|time)"
    r")"
    r"[^.!?]*[.!?]?",
    flags=re.IGNORECASE,
)


def _strip_booking_redirect(text: str) -> str:
    """Remove a trailing "back to your booking" push from an answer.

    Only the redirect is removed, and only when it is a separate sentence.
    A reply whose entire content IS the redirect is left alone, because the
    caller then substitutes the standard step prompt instead -- removing it
    there would leave the customer with nothing.
    """
    raw = str(text or "").strip()
    if not raw:
        return raw
    cleaned = _BOOKING_REDIRECT_SENTENCE_RE.sub("", raw)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    # A short answer plus a push is still an answer. The length floor here is
    # deliberately much lower than the refusal path's: a complete answer can
    # be one sentence ("The capital of Japan is Tokyo."), and falling back to
    # the original would put the push straight back in front of the customer.
    if len(cleaned) < 20:
        return raw
    return cleaned


# Internal vocabulary never shown to a customer. Removed from whatever
# material is kept, so a partial answer loses the reference but not the fact.
_SCAFFOLDING_RE = re.compile(
    r"\b(?:knowledge\s*(?:base|materials?|documents?|sources?)|"
    r"our\s+(?:documents?|materials?|website\s+content)|the\s+materials?|"
    r"the\s+documents?|retrieved\s+(?:content|chunks?)|excerpts?|"
    r"materials?\s+(?:don't|do\s+not)|documents?\s+(?:don't|do\s+not))\b",
    flags=re.IGNORECASE,
)


def _strip_internal_scaffolding(text: str) -> str:
    """Drop references to the retrieval machinery, keep the facts."""
    cleaned = _SCAFFOLDING_RE.sub("", str(text or ""))
    # Tidy the seams left by the removal.
    cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
    cleaned = re.sub(r"[\s,;.]+([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _normalise_offtopic_decline(reply: str) -> str:
    """Return one consistent, warm decline for every off-topic question.

    The model invents a new cold phrasing on nearly every call ("...in our
    knowledge materials", "the company knowledge materials don't include...").
    Those mention internal machinery the customer has never heard of and read
    as a brush-off. A genuine knowledge ANSWER is left untouched — only a
    decline is rewritten, so nothing that was actually looked up is lost.

    A PARTIAL answer keeps its facts. "We offer Kentico development. I don't
    have information about timelines." contains a real answer plus a caveat,
    and replacing the whole thing threw away the Kentico part. Earlier this
    function matched the caveat anywhere in the text and discarded
    everything, which is worse for the customer than the cold phrasing it was
    meant to fix.
    """
    text = str(reply or "").strip()
    if not text:
        return _OFFTOPIC_REPLY

    # A courtesy opener is not itself part of the refusal, so it is removed
    # before judging. "Sorry, I don't have information about this." otherwise
    # reads as an answer and reaches the customer verbatim.
    text = _COURTESY_PREFIX_RE.sub("", text).strip()
    if not text:
        return _OFFTOPIC_REPLY

    # A refusal is a reply that OPENS with one. A partial answer also
    # contains a refusal, but only after the useful part, and the useful part
    # is what the customer came for.
    lowered = text.lower()
    opens_with_refusal = any(lowered.startswith(p) for p in _REFUSAL_OPENERS)
    if not opens_with_refusal:
        return text  # a real answer, leave it exactly as it is

    # Strip the refusal sentence, and see whether real content remains.
    _split = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    remainder = _split[1] if len(_split) > 1 else ""
    cleaned = _strip_internal_scaffolding(remainder)
    if len(cleaned) >= _MIN_ANSWER_CHARS:
        return f"{cleaned}\n\n{_OFFTOPIC_REPLY}"

    return _OFFTOPIC_REPLY


def _unmatched_time_choice(message_text: str, offered_labels) -> str:
    """Return the customer's rejected time wording, or "" if this is not a time.

    Used to say "that time isn't valid" rather than silently repeating the
    option list. Deliberately conservative: it only speaks up when the message
    clearly *is* a clock time and clearly is none of the options offered, so
    ordinary replies ("thanks", "3 of us") are never second-guessed.
    """
    raw = str(message_text or "").strip()
    if not raw or not offered_labels:
        return ""
    # Must contain a time token.
    _norm = raw.replace(".", ":")
    if not (
        re.search(r"\b\d{1,2}\s*[ap]\.?m\.?\b", _norm, flags=re.IGNORECASE)
        or re.fullmatch(r"\d{1,2}:\d{2}", raw)
    ):
        return ""

    def _canon(value: str) -> str:
        """Normalise a clock time so '12pm', '12:00' and '12:00 PM' all agree.

        A bare '12:00' carries no meridiem, so it is compared against BOTH
        interpretations of every offered slot. Otherwise a customer who types
        '12:00' for a 12:00 PM slot would be told the time was invalid.
        """
        v = str(value or "").strip().lower().replace(".", "")
        v = re.sub(r"\s*([ap])\.?\s*m\.?\b", r"\1m", v)
        m = re.fullmatch(r"(\d{1,2}):?(\d{2})?(am|pm)?", v)
        if not m:
            return re.sub(r"\s+", "", v)
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = m.group(3)
        if meridiem is None:
            # Ambiguous: 12 could be noon or midnight, 7 could be AM or PM.
            if hour == 12:
                return f"{hour:02d}{minute:02d}am/{hour:02d}{minute:02d}pm"
            return f"{hour:02d}{minute:02d}am/{hour:02d}{minute:02d}pm"
        return f"{hour:02d}{minute:02d}{meridiem}"

    offered = set()
    for label in offered_labels:
        canon = _canon(label)
        offered.add(canon)
        # An ambiguous entry is stored as "hhmmam/hhmnpm" so it can match
        # either meridiem spelling of the same clock time.
        offered.update(part for part in canon.split("/") if part)
    chosen = _canon(raw)
    if chosen in offered or any(part in offered for part in chosen.split("/") if part):
        return ""
    return raw


def _active_effect_state(chat_session: ChatSessionState) -> Dict[str, Any]:
    data = dict(chat_session.collected_data or {})
    state = data.get("_active_path_effects")
    if not isinstance(state, dict):
        state = {}
    data["_active_path_effects"] = state
    chat_session.collected_data = data
    return state


_RESEND_PATTERN = re.compile(
    r"\b(resend|re-?send|recend|resent|resent otp|recend otp|send\s+(?:it|the\s+code|again)|new\s+code|didn'?t\s+(?:get|receiv(?:e|ed))|did\s+not\s+(?:get|receiv(?:e|ed))|haven'?t\s+(?:receiv(?:e|ed)|got|get)|hasn'?t\s+(?:receiv(?:e|ed)|got)|not\s+(?:receiv(?:e|ed)|got|getting)|never\s+(?:receiv(?:e|ed)|got|receive)|no\s+(?:otp|code)|not\s+received|waiting\s+for\s+(?:otp|code)|where.*otp|otp\s+not\s+received)\b",
    re.IGNORECASE,
)


def _is_resend_request(message_text: str) -> bool:
    text = str(message_text or "").strip()
    if not text:
        return False
    if _RESEND_PATTERN.search(text):
        return True
    # Heuristic: message mentions otp/code AND a negation / waiting intent
    # e.g. "I didn't received any otp" -> contains otp + didn't + received
    low = text.lower()
    has_otp = ("otp" in low or "code" in low or "whatsapp" in low)
    has_neg = any(
        kw in low
        for kw in (
            "didn't",
            "didnt",
            "did not",
            "haven't",
            "havent",
            "hasn't",
            "hasnt",
            "not received",
            "not got",
            "never",
            "no otp",
            "no code",
            "not getting",
            "waiting",
            "where",
            "didn't receive",
            "didnt receive",
            "didn't received",
            "didnt received",
        )
    )
    return bool(has_otp and has_neg)


def _is_awaiting_otp(chat_session: ChatSessionState) -> bool:
    # Check both email and phone OTP pending — WhatsApp uses phone channel
    if getattr(chat_session, "otp_verified", False) and getattr(chat_session, "phone_otp_verified", False):
        return False
    # If either email or phone OTP is pending, consider awaiting
    # For phone-only orgs (Priyanshu), phone_otp_verified is the gate
    # NOTE: ChatSessionState stores everything in collected_data (alias for state_data).
    # _get_policy_state expects .state_data, so we try both attributes.
    try:
        from app.crud.otp import _get_policy_state as _get_ps

        for ch in ("email", "phone"):
            ps = {}
            if hasattr(chat_session, "state_data"):
                try:
                    ps = _get_ps(chat_session, ch)
                except Exception:
                    ps = {}
            if not ps or not ps.get("last_sent_at"):
                # Fallback: ChatSessionState uses collected_data
                sd = getattr(chat_session, "collected_data", None) or getattr(
                    chat_session, "state_data", None
                )
                if isinstance(sd, dict):
                    key = "_otp_phone_policy" if ch == "phone" else "_otp_policy"
                    cand = sd.get(key)
                    if isinstance(cand, dict):
                        ps = cand
            if isinstance(ps, dict) and ps.get("last_sent_at"):
                return True
    except Exception:
        pass
    effect_state = _active_effect_state(chat_session)
    for key in ("otp_sent_keys", "phone_otp_sent_keys", "otp_keys", "phone_otp_keys"):
        sent_keys = effect_state.get(key)
        if isinstance(sent_keys, list) and len(sent_keys) > 0:
            return True
    # Fallback: if phone or email verification is pending and OTP was recently sent (state_data has policy)
    try:
        sd = getattr(chat_session, "state_data", None) or getattr(chat_session, "collected_data", None) or {}
        if isinstance(sd, dict):
            for k in ("_otp_policy", "_otp_phone_policy", "otp_sent_keys", "phone_otp_sent_keys"):
                v = sd.get(k)
                if isinstance(v, dict) and v.get("last_sent_at"):
                    return True
                if isinstance(v, list) and len(v) > 0:
                    return True
    except Exception:
        pass
    # Legacy fallback
    sent_keys = effect_state.get("otp_sent_keys")
    return isinstance(sent_keys, list) and len(sent_keys) > 0


async def _handle_graph_otp_resend(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
    org_config: Optional[OrgConfig] = None,
) -> ActionPayload:
    # Determine pending channel from org config + verification state — never fallback to disabled channel
    # WhatsApp-only: phone if pending, else email if pending, else error
    _pending: Optional[str] = None
    try:
        _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
        _org_config = org_config
        if _org_config is None:
            try:
                from sqlalchemy.future import select as _sel
                from app.models.config import OrgConfig

                _res = await db.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
                _org_config = _res.scalars().first()
            except Exception:
                _org_config = None
        if _org_config is not None:
            # NULL flag = schema default (email ON, phone OFF).
            _raw_e = getattr(_org_config, "require_otp", True)
            _raw_p = getattr(_org_config, "require_phone_otp", False)
            _req_email = True if _raw_e is None else bool(_raw_e)
            _req_phone = False if _raw_p is None else bool(_raw_p)
            _phone_pending = _req_phone and not bool(getattr(chat_session, "phone_otp_verified", False))
            _email_pending = _req_email and not bool(getattr(chat_session, "otp_verified", False))
            if _phone_pending and _email_pending:
                _pending = "phone" if _is_whatsapp else "email"
            elif _phone_pending:
                _pending = "phone"
            elif _email_pending:
                _pending = "email"
            else:
                return ActionPayload(
                    message="OTP verification is not required at this time.",
                    widget_directive="TEXT_INPUT",
                )
            # If pending is email but email disabled (should not happen due to above), guard
            if _pending == "email" and not _req_email:
                return ActionPayload(
                    message="Email OTP is disabled for this organization. Please contact support.",
                    widget_directive="TEXT_INPUT",
                )
            if _pending == "phone" and not _req_phone:
                return ActionPayload(
                    message="Phone OTP is disabled for this organization.",
                    widget_directive="TEXT_INPUT",
                )
    except Exception:
        logger.warning("graph resend channel resolution failed", exc_info=True)
    if _pending is not None:
        return await message_processor._handle_otp_resend(
            db, org, chat_session, message_text, background_tasks, channel=_pending
        )
    # Fallback: let message_processor decide (defaults to email, but will now respect pending)
    return await message_processor._handle_otp_resend(
        db, org, chat_session, message_text, background_tasks
    )


def _normalize_event_component(raw_value: Any) -> str:
    return str(raw_value or "").strip().lower()


def _build_confirmation_event_key(session_id: str, collected_data: Dict[str, Any]) -> str:
    booking_id = _normalize_event_component(collected_data.get("booking_id"))
    if booking_id:
        return f"confirmation:{session_id}:{booking_id}"
    email = _normalize_event_component(collected_data.get("email"))
    date_value = _normalize_event_component(collected_data.get("date"))
    time_value = _normalize_event_component(collected_data.get("time"))
    return f"confirmation:{session_id}:{email}:{date_value}:{time_value}"


def _normalize_collected_data(raw_collected_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(raw_collected_data, dict):
        return {}
    sanitized = dict(raw_collected_data)
    sanitized.pop(crud_chat.RUNTIME_CHECKPOINT_KEY, None)
    return sanitized


def _normalize_context_window(raw_window: Any) -> list[dict[str, str]]:
    if not isinstance(raw_window, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in raw_window:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = item.get("content")
        if role not in {"human", "ai", "user", "assistant"}:
            continue
        if content is None:
            continue
        normalized.append({"role": role, "content": str(content)})
    return normalized


def _window_item_to_message(item: dict[str, str]) -> HumanMessage | AIMessage:
    role = item.get("role", "human")
    content = item.get("content", "")
    if role in {"ai", "assistant"}:
        return AIMessage(content=content)
    return HumanMessage(content=content)


def _load_runtime_context(chat_session: ChatSessionState) -> tuple[str, list[dict[str, str]]]:
    raw_state = {}
    collected_data = getattr(chat_session, "collected_data", None)
    if isinstance(collected_data, dict):
        checkpoint = collected_data.get(crud_chat.RUNTIME_CHECKPOINT_KEY, {})
        if isinstance(checkpoint, dict):
            raw_state = checkpoint.get("state", {}) or {}
    summary = str(raw_state.get("summary", "") or "")
    context_window = _normalize_context_window(raw_state.get("context_window"))
    return summary, context_window


def _resolve_field_definitions_snapshot(
    chat_session: ChatSessionState,
    org_config_dict: Dict[str, Any],
) -> list[FieldDefinition]:
    snapshot_raw = {}
    if isinstance(chat_session.collected_data, dict):
        snapshot_raw = chat_session.collected_data.get(
            crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY
        )
    source = snapshot_raw or org_config_dict.get("field_definitions") or org_config_dict.get(
        "required_fields"
    )
    return order_field_definitions(parse_field_definitions(source))


def _allows_optional_contact_fields(org_config_dict: Dict[str, Any]) -> bool:
    custom_prompts = org_config_dict.get("custom_prompts")
    if not isinstance(custom_prompts, dict):
        return False
    return custom_prompts.get("allow_optional_contact_fields") is True


def _enforce_runtime_contact_requirements(
    follow_up_fields: list[str],
    collected_data: Dict[str, Any],
    org_config_dict: Dict[str, Any],
) -> list[str]:
    if _allows_optional_contact_fields(org_config_dict):
        return follow_up_fields
    if follow_up_fields:
        return follow_up_fields
    required = list(follow_up_fields)
    configured_source = (
        org_config_dict.get("field_definitions") or org_config_dict.get("required_fields")
    )
    configured_field_definitions = order_field_definitions(
        parse_field_definitions(configured_source)
    )
    required_contacts = {
        field_definition.name
        for field_definition in configured_field_definitions
        if field_definition.required and field_definition.name in {"email", "phone"}
    }
    missing_contacts: list[str] = []
    for field in ("email", "phone"):
        if field not in required_contacts:
            continue
        value = collected_data.get(field)
        has_value = bool(str(value).strip()) if isinstance(value, str) else value is not None
        if not has_value and field not in required:
            missing_contacts.append(field)
    required = missing_contacts + required
    return required


def _is_non_terminal_directive(directive: str) -> bool:
    return directive in {
        "TEXT_INPUT",
        "REQUEST_CONFIRMATION",
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_PHONE_INPUT",
        "REQUIRE_EMAIL_INPUT",
        "REQUIRE_SELECT",
        "REQUIRE_TEXTAREA",
        "REQUIRE_NUMBER_INPUT",
        "REQUIRE_OTP_MODAL",
        "REQUIRE_CANCEL_LOOKUP",
    }


def _guard_user_visible_booking_message(
    *,
    assistant_message: str,
    directive: str,
    require_otp: bool,
    chat_session: ChatSessionState,
    field_meta: Dict[str, Any],
    fd_map: Dict[str, FieldDefinition],
) -> str:
    if directive == "BOOKING_CONFIRMED_CARD":
        return assistant_message

    if directive == "CANCELLED_CONFIRMATION_CARD":
        return assistant_message

    active_field = str((field_meta or {}).get("name") or "").strip()
    invalid_error = ""
    if active_field:
        merge_diag = (field_meta or {}).get("merge_diagnostics")
        if isinstance(merge_diag, dict):
            invalid_fields = merge_diag.get("invalid_fields")
            if isinstance(invalid_fields, dict) and active_field in invalid_fields:
                invalid_error = str(invalid_fields.get(active_field) or "")
        if not invalid_error:
            validation_error = (field_meta or {}).get("validation_error")
            if isinstance(validation_error, str):
                invalid_error = validation_error.strip()
    if (
        invalid_error
        and looks_like_field_saved_claim(assistant_message, active_field)
    ):
        label = str(
            (field_meta or {}).get("label") or active_field.replace("_", " ")
        ).lower()
        return f"I couldn't save that {label}. {invalid_error}"

    if not looks_like_confirmation_claim(assistant_message):
        return assistant_message

    if directive in {
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_PHONE_INPUT",
        "REQUIRE_EMAIL_INPUT",
        "REQUIRE_SELECT",
        "REQUIRE_TEXTAREA",
        "REQUIRE_NUMBER_INPUT",
    }:
        return message_processor._build_context_aware_response(
            directive=directive,
            field_meta=field_meta,
            collected_data=dict(chat_session.collected_data or {}),
            valid_data={},
            errors={},
            llm_generated_msg=assistant_message,
        )

    if require_otp and not chat_session.otp_verified:
        email = resolve_email_from_collected_data(
            chat_session.collected_data,
            list(fd_map.values()) if isinstance(fd_map, dict) else None,
        )
        if email:
            return (
                f"Before I can confirm your booking, please verify the OTP sent to {email}."
            )
        return "Before I can confirm your booking, please verify your OTP code."

    if directive == "REQUEST_CONFIRMATION":
        summary_message, _ = message_processor._build_confirmation_summary(chat_session, fd_map)
        return summary_message

    if _is_non_terminal_directive(directive):
        return message_processor._build_context_aware_response(
            directive=directive,
            field_meta=field_meta,
            collected_data=dict(chat_session.collected_data or {}),
            valid_data={},
            errors={},
            llm_generated_msg=assistant_message,
        )

    return "I still need to complete your booking before I can confirm it."

_POST_BOOKING_ACTION_THRESHOLD = 0.70


async def _llm_post_booking_analysis(
    message_text: str, status: str, pending_cancel: bool, collected_data: Dict[str, Any]
) -> Dict[str, Any]:
    """LLM-based analysis of a message on a finished session. The model
    classifies intent AND writes the conversational reply in one call —
    no hardcoded phrase matching."""
    import json as _json

    prompt = f"""Analyze the user message in a booking support conversation.

SESSION_STATUS: {status}
PENDING_CANCEL_CONFIRMATION: {str(pending_cancel).lower()}
BOOKING_DETAILS: {_json.dumps(collected_data or {}, default=str)}

USER_MESSAGE: "{message_text}"

Classify the user's intent. Use ONLY the message's actual meaning:

- classification "ACTION_REQUEST": the user is clearly stating they want to
  perform cancel/reschedule/rebook RIGHT NOW. Examples: "cancel my booking",
  "I want to cancel my booking", "please cancel it", "reschedule",
  "I need to reschedule", "book again".
- classification "INFO_QUESTION": the user only asks HOW/WHETHER something is
  possible, not that they want it done now. Examples: "how do I cancel?",
  "can I change the time?".
- classification "CONFIRM": user confirms a previously asked action.
- classification "DENY": user declines a previously asked action.
- classification "ACKNOWLEDGMENT": thanks/ok/sure/acknowledgment, not a request.
- classification "NEUTRAL": anything else.

action is "CANCEL", "RESCHEDULE", or "REBOOK" ONLY for ACTION_REQUEST;
otherwise "NONE".

IMPORTANT: A user who says "I want to cancel my booking" (or similar direct
request) IS making an ACTION_REQUEST — do NOT treat it as an info question and
do NOT reply asking whether they want to proceed.

If no action will be executed, write "reply": a warm 1-2 sentence assistant
reply that is accurate for the situation (booking confirmed/cancelled/expired),
answers the user's question if any, and mentions they can say "cancel my
booking" / "reschedule" when the booking is confirmed.

Return ONLY valid JSON:
{{"classification": "ACTION_REQUEST|INFO_QUESTION|CONFIRM|DENY|ACKNOWLEDGMENT|NEUTRAL",
  "action": "CANCEL|RESCHEDULE|REBOOK|NONE",
  "confidence": 0.0,
  "reply": "..."}}
"""
    try:
        result = await message_processor.llm_engine._call_llm(
            prompt,
            system_prompt="You are a strict booking-support intent classifier. Output JSON only.",
            json_mode=True,
        )
        payload = _json.loads(str(result or ""))
        classification = str(payload.get("classification", "NEUTRAL")).upper()
        action = str(payload.get("action", "NONE")).upper()
        confidence = float(payload.get("confidence", 0.0))
        reply = str(payload.get("reply") or "").strip()
        return {
            "classification": classification,
            "action": action,
            "confidence": confidence,
            "reply": reply,
        }
    except Exception:
        logger.warning("post_booking LLM analysis failed", exc_info=True)
        return {
            "classification": "NEUTRAL",
            "action": "NONE",
            "confidence": 0.0,
            "reply": "",
        }


async def _handle_post_booking_session(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """
    Handling for messages on finished sessions (CONFIRMED / CANCELLED /
    EXPIRED). The LLM analyzes each message automatically (intent + reply)
    and the result is dispatched deterministically — no hardcoded phrases,
    and the booking flow is never re-entered.
    """
    status = chat_session.session_status

    # Deterministic confirm short-circuit: on a finished CONFIRMED booking a
    # confirm word is never a new request — answer from stored state without
    # spending an LLM call (whose free-form reply caused the "different
    # confirmation every time" loop). Cancel-flow confirmations are excluded:
    # a "yes" while cancellation is pending must enter the cancel flow.
    if status == "CONFIRMED" and not chat_session.pending_cancel:
        try:
            from app.engine.time_flex import is_pure_confirm_message as _is_confirm_msg

            _looks_confirm = bool(_is_confirm_msg(message_text))
        except Exception:
            _looks_confirm = False
        if _looks_confirm:
            reply = message_processor.build_finished_session_reply(chat_session, status)
            action_payload = ActionPayload(
                message=reply, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _persist_transcript_best_effort(
                db,
                session_id=chat_session.session_id,
                user_message=message_text,
                assistant_message=reply,
                directive="TEXT_INPUT",
                intent=chat_session.current_intent,
            )
            return action_payload

    # LLM analysis of the user's actual message.
    analysis = await _llm_post_booking_analysis(
        message_text=message_text,
        status=status,
        pending_cancel=bool(chat_session.pending_cancel),
        collected_data=dict(chat_session.collected_data or {}),
    )
    classification = str(analysis.get("classification") or "NEUTRAL").upper()
    action = str(analysis.get("action") or "NONE").upper()
    confidence = float(analysis.get("confidence") or 0.0)
    reply = str(analysis.get("reply") or "").strip()
    is_action_request = (
        confidence >= _POST_BOOKING_ACTION_THRESHOLD
        and action in ("CANCEL", "RESCHEDULE", "REBOOK")
    )

    # 1. Explicit cancel request on a CONFIRMED booking → OTP-protected cancel
    #    flow (lookup → cancel-scoped OTP → execution).
    if status == "CONFIRMED" and is_action_request and action == "CANCEL":
        action_payload = await cancel_flow.start_cancel_flow(db, chat_session, message_text)
        await _persist_transcript_best_effort(
            db,
            session_id=chat_session.session_id,
            user_message=message_text,
            assistant_message=action_payload.message,
            directive=action_payload.widget_directive or "TEXT_INPUT",
            intent="CANCEL_BOOKING",
        )
        return action_payload

    # 2. Explicit reschedule request on a CONFIRMED booking → cancel slot +
    #    reopen flow, keeping name/email/phone (date/time re-collected).
    if status == "CONFIRMED" and is_action_request and action == "RESCHEDULE":
        return await message_processor._reschedule_booking(
            db,
            org,
            chat_session,
            background_tasks,
            user_message=message_text,
        )

    # 3. Confirmation / denial of a previously asked cancellation.
    # OTP-gated: a plain "yes" must NOT delete the booking — route into the
    # OTP-protected cancel flow (lookup → verification code → delete).
    if status == "CONFIRMED" and chat_session.pending_cancel:
        if classification == "CONFIRM":
            chat_session.pending_cancel = False
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            action_payload = await cancel_flow.start_cancel_flow(
                db, chat_session, message_text
            )
            await _persist_transcript_best_effort(
                db,
                session_id=chat_session.session_id,
                user_message=message_text,
                assistant_message=action_payload.message,
                directive=action_payload.widget_directive or "TEXT_INPUT",
                intent="CANCEL_BOOKING",
            )
            return action_payload
        if classification == "DENY":
            chat_session.pending_cancel = False
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            msg = "No worries — your booking stays as is! Let me know if you need anything else. 😊"
            action_payload = ActionPayload(
                message=msg, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _persist_transcript_best_effort(
                db,
                session_id=chat_session.session_id,
                user_message=message_text,
                assistant_message=msg,
                directive="TEXT_INPUT",
                intent=chat_session.current_intent,
            )
            return action_payload

    # 4. Rebook from CANCELLED / EXPIRED → fresh booking state.
    if status in ("CANCELLED", "EXPIRED") and action == "REBOOK" and (
        is_action_request or classification in ("CONFIRM", "NEUTRAL")
    ):
        cancel_flow.reset_for_new_booking(chat_session)
        await cancel_flow._persist_session(
            db, chat_session, remove_keys=cancel_flow.SESSION_RESET_REMOVE_KEYS
        )
        try:
            from app.engine.agent.graph import memory as _graph_memory
            await _graph_memory.adelete_thread(chat_session.session_id)
        except Exception as _thread_err:
            logger.warning(
                "graph_thread_reset_failed",
                extra={"session_id": chat_session.session_id, "error": str(_thread_err)},
            )
        msg = "Hello! 👋 I'd be happy to help you book a new appointment. What's your full name?"
        action_payload = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", is_terminal=False
        )
        await _persist_transcript_best_effort(
            db,
            session_id=chat_session.session_id,
            user_message=message_text,
            assistant_message=msg,
            directive="TEXT_INPUT",
            intent="START_BOOKING",
        )
        return action_payload

    # 5. Everything else → deterministic status reply, except genuine
    #    questions which keep the LLM's answer. Echoing free-form LLM text
    #    here produced a different "confirmation" on every repeat and let
    #    misclassified words ("Human") invent new confirmations.
    #    Always TEXT_INPUT; never a booking-collection widget.
    if classification == "INFO_QUESTION" and reply:
        pass
    elif (status == "CONFIRMED" and classification == "CONFIRM") or (
        reply and looks_like_confirmation_claim(reply)
    ):
        reply = message_processor.build_finished_session_reply(chat_session, status)
    elif not reply:
        reply = {
            "CONFIRMED": "Your booking is confirmed! You can say 'cancel my booking' or 'reschedule' anytime.",
            "CANCELLED": "Your previous booking was cancelled. Just say 'I want to book' to start a new one!",
            "EXPIRED": "This session expired. Say 'I want to book' to start a new booking!",
        }.get(status, "Is there anything else I can help you with?")
    action_payload = ActionPayload(
        message=reply, widget_directive="TEXT_INPUT", is_terminal=False
    )
    await _persist_transcript_best_effort(
        db,
        session_id=chat_session.session_id,
        user_message=message_text,
        assistant_message=reply,
        directive="TEXT_INPUT",
        intent=chat_session.current_intent,
    )
    return action_payload


def resolve_otp_verify_channels(*args, **kwargs):
    """Backward-compatible alias (logic lives in app.engine.otp_guards)."""
    from app.engine.otp_guards import (
        resolve_otp_verify_channels as _resolve,
    )

    return _resolve(*args, **kwargs)


def apply_verified_phone(collected, verified_phone):
    """Backward-compatible alias (logic lives in app.engine.otp_guards)."""
    from app.engine.otp_guards import apply_verified_phone as _apply

    return _apply(collected, verified_phone)


_CONFIRM_TOKENS_FOR_BOOKING = frozenset({
    "yes", "yep", "yup", "yeah", "yup", "ok", "okay", "okey", "sure", "confirm",
    "confirmed", "book", "booked", "proceed", "go", "ahead", "done", "affirmative",
    "correct", "right", "fine", "alright", "allright", "absolutely", "definitely",
    "lock", "finalise", "finalize", "schedule",
})


_CONFIRM_FILLER_WORDS = frozenset({
    "it", "in", "on", "my", "the", "a", "an", "please", "is", "are", "be",
    "up", "out", "for", "me", "us", "that", "this", "i", "we", "and", "to",
    "do", "did", "would", "like", "want", "ok", "okay",
    # The object being confirmed: "confirm booking", "book my appointment".
    # Harmless on their own because every OTHER token must still read as a
    # confirmation, so "cancel booking" / "delete my appointment" stay false.
    "booking", "appointment", "reservation", "order", "slot", "session",
    "meeting", "details", "detail", "info", "information",
})


def may_send_otp_standalone(
    *,
    booking_gate_handled: bool,
    otp_required: bool,
    user_confirmed: Optional[bool],
) -> bool:
    """May the standalone OTP path send a code on this turn?

    Invariant: a verification code is never sent before the customer has
    explicitly confirmed the booking. The gate (`should_run_booking_gate`)
    already refused to run the booking flow, but a second, independent OTP
    path went ahead and sent the code anyway -- so a customer who filled in
    their details and then asked a question was sent a code for a booking
    they had not agreed to.

    `user_confirmed=None` means the caller cannot report a confirmation, and
    keeps the previous behaviour rather than silently suppressing the code.
    """
    if booking_gate_handled:
        return False
    if not otp_required:
        return False
    if user_confirmed is None:
        return True
    return bool(user_confirmed)


def _is_user_confirmation(message_text: str) -> bool:
    """True when the customer's own words confirm the booking.

    This is the authorisation signal for creating a booking, so it is derived
    only from the inbound text and never from the model's intent label. When
    the model returned an unexpected intent on the confirmation turn the
    booking was silently skipped while the customer was still told "confirmed".

    Real customers type "Okayy", "yess", "okk" or "comfirm". Those must count
    as a confirmation here, otherwise the flow stalls at the final step. The
    fuzzy allowance is deliberately narrow to avoid reopening the original
    defect, where substring matching treated any sentence containing the letter
    k ("I want to know about dotstark") as a confirmation:
      * at most three words, so a full sentence can never match
      * no question mark, so a question is never a confirmation
      * every alphabetic token must be a known confirm token or within one
        edit of one
    """
    text = str(message_text or "").strip()
    if not text or "?" in text:
        return False
    # A bare six-digit code is verified by the OTP branch, not a confirmation.
    if re.fullmatch(r"\d{6}", text):
        return False

    import difflib as _difflib

    tokens = re.findall(r"[a-z']+", text.lower())
    if not tokens or len(tokens) > 3:
        return False

    # Interactive buttons arrive as their ID, not as prose: tapping "Confirm"
    # on WhatsApp sends "btn_confirm" (see whatsapp_client.format_action_for_
    # whatsapp). A natural-language-only check rejected those, so clicking
    # Confirm re-displayed the summary forever instead of moving on to
    # verification. Underscores and emoji are stripped before matching, and a
    # cancel/edit tap is explicitly excluded.
    _norm = re.sub(r"[^a-z]+", " ", text.lower())
    if "confirm" in _norm.split():
        if not re.search(r"\b(?:cancel|edit|change|modify|back)\b", _norm):
            return True

    for token in tokens:
        if token in _CONFIRM_TOKENS_FOR_BOOKING or token in _CONFIRM_FILLER_WORDS:
            continue
        if any(
            _difflib.SequenceMatcher(None, token, known).ratio() >= 0.8
            for known in _CONFIRM_TOKENS_FOR_BOOKING
            if abs(len(known) - len(token)) <= 2
        ):
            continue
        return False
    return True


def should_run_booking_gate(
    *,
    directive: str,
    missing_fields,
    require_otp: bool,
    require_phone_otp: bool,
    otp_verified: bool,
    phone_otp_verified: bool,
    user_confirmed: Optional[bool] = None,
) -> bool:
    """Whether the booking/OTP gate must run for this turn.

    ``require_otp`` / ``require_phone_otp`` MUST be the admin's configuration
    values, not the derived "still pending" flags. The derived flags fold in
    ``not verified``, so they flip to False the moment a code is accepted — and
    an earlier version of this gate used them to decide whether to run at all.
    That disabled the gate on exactly the turn the booking was supposed to
    happen: the customer verified, the booking was silently skipped, and the
    model's "your booking is confirmed" text was the only thing left.

    ``user_confirmed`` preserves the required order: details -> summary ->
    confirm -> OTP -> code -> booking. Until the customer has actually
    confirmed, the gate must not run, because running it would send the OTP
    before the customer has agreed to the booking. With None (callers that
    cannot report a confirmation) the previous behaviour is kept.
    """
    if not (require_otp or require_phone_otp):
        return False
    _otp_satisfied = bool(
        (require_otp and otp_verified) or (require_phone_otp and phone_otp_verified)
    )
    if user_confirmed is False and not missing_fields and not _otp_satisfied:
        # Details complete, customer has NOT confirmed, and no code has been
        # accepted yet: show the summary and wait. Do not send a code.
        #
        # Once the code IS accepted the customer has already confirmed (the
        # code is only ever sent after an explicit confirmation), so the gate
        # must run and create the booking. Blocking here on the code turn would
        # re-show the summary forever and nothing would ever be booked.
        return False
    if directive == "PROCESS_BOOKING":
        return True
    otp_pending = bool(
        (require_otp and not otp_verified)
        or (require_phone_otp and not phone_otp_verified)
    )
    if not otp_pending:
        # Every required code has been accepted. This is the turn the booking
        # must actually be written, so the gate MUST run and hand off to
        # _handle_booking_flow -> _execute_booking.
        #
        # Returning False here meant the code was accepted, the flag was
        # persisted, the gate declined to run, the graph answered instead, and
        # the customer got "your appointment is confirmed" with no Booking
        # row, no email and no notification behind it.
        logger.info(
            "BOOKING GATE: OTP satisfied -> run gate (missing=%s)",
            list(missing_fields or []),
        )
        return not missing_fields
    if missing_fields:
        return False
    return directive in (
        "PROCESS_BOOKING",
        "REQUEST_CONFIRMATION",
        "BOOKING_SUMMARY_CARD",
        "TEXT_INPUT",
    )


async def _session_has_persisted_booking(
    db: AsyncSession,
    session_id: str,
    *,
    org_id=None,
    collected_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when a Booking row exists for this booking attempt.

    Used to make "you are booked" a verifiable claim rather than a generated
    sentence.

    Matching deliberately does NOT rely on ``bookings.session_id`` alone. That
    column is nullable and in practice is NULL for most WhatsApp bookings
    (only 9 of 48 rows carry it), so a session-only lookup fails to find
    genuine bookings and would wrongly suppress every real confirmation.
    Three strategies are tried in order:
      1. an explicit booking id already resolved by the caller
      2. session id, when one was recorded
      3. org + customer identity within a short recent window

    Fails closed: on error it returns False, so a confirmation is withheld
    rather than risk telling a customer they have an appointment that does
    not exist.
    """
    collected = collected_data or {}
    from sqlalchemy import or_ as _or_
    from sqlalchemy.future import select as _sel
    from app.models.booking import Booking as _Booking

    def _one(stmt):
        return stmt.limit(1)

    # 1. Explicit booking id resolved earlier in this turn.
    explicit_id = str(collected.get("booking_id") or "").strip()
    if explicit_id:
        try:
            row = (
                await db.execute(
                    _one(_sel(_Booking.id).where(_Booking.id == uuid.UUID(explicit_id)))
                )
            ).first()
            if row:
                return True
        except Exception:
            pass

    # 2. Session id, when the booking recorded one.
    if session_id:
        try:
            row = (
                await db.execute(
                    _one(
                        _sel(_Booking.id).where(
                            _Booking.session_id == uuid.UUID(str(session_id))
                        )
                    )
                )
            ).first()
            if row:
                return True
        except Exception:
            pass

    # 3. Identity match inside a recent window, so an old booking for the same
    #    person cannot justify a fresh confirmation claim.
    email = str(collected.get("email") or "").strip().lower()
    phone = str(collected.get("phone") or "").strip()
    identity = []
    if email:
        identity.append(_Booking.customer_email == email)
    if phone:
        identity.append(_Booking.customer_phone == phone)
    if identity and org_id is not None:
        try:
            row = (
                await db.execute(
                    _one(
                        _sel(_Booking.id).where(
                            _Booking.org_id == org_id,
                            _or_(*identity),
                            _Booking.created_at
                            >= datetime.now(timezone.utc) - timedelta(minutes=30),
                        )
                    )
                )
            ).first()
            if row:
                return True
        except Exception:
            pass
    return False


async def _guard_confirmation_claim(
    db: AsyncSession,
    session_id: str,
    assistant_message: str,
    directive: str,
    chat_session: Any,
    field_meta: Dict[str, Any],
    org: Any,
    fd_map: Dict[str, Any],
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """Block a confirmation claim unless a Booking row actually exists.

    Required order is details -> summary -> confirm -> OTP -> booking, and
    nothing may say "booked" or "confirmed" until a Booking row is on disk.

    This lives as a helper because the guard used to sit only near the end of
    `process_turn`, where its own comment claimed it covered "every path,
    including the ones that return early". It did not. Production evidence:
    with zero Booking rows for a session, the agent sent "Your appointment is
    booked for October 2, 2026 at 3:00 PM" and then, once the code arrived,
    "Your booking is already processed." Both came from paths that return
    before the old guard could run.

    Every early return of a model-authored message must call this. Returns
    ``(message, directive, field_meta)`` when it downgraded, else ``None``.
    """
    if not isinstance(assistant_message, str) or not assistant_message.strip():
        return None
    try:
        from app.engine.user_visible_text import looks_like_confirmation_claim

        _claims = looks_like_confirmation_claim(assistant_message)
    except Exception:
        _claims = False
    if not _claims:
        return None
    if await _session_has_persisted_booking(
        db,
        session_id,
        org_id=getattr(org, "id", None),
        collected_data=dict(chat_session.collected_data or {}),
    ):
        return None
    logger.warning(
        "confirmation_claim_downgraded_no_booking session=%s directive=%s",
        str(session_id)[:8],
        directive,
    )
    _msg, _dir = message_processor._build_confirmation_summary(chat_session, fd_map)
    field_meta.pop("knowledge_answer", None)
    return _msg, _dir, field_meta



async def process_message_with_graph(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
    recovery_metadata: Optional[Dict[str, Any]] = None,
    raise_on_timeout: bool = False,
    transcript_user_text: Optional[str] = None,
    inbound_message_id: Optional[str] = None,
) -> ActionPayload:
    """
    Entry point for the LangGraph-based AI agent.
    Replaces the legacy process_message state machine.

    transcript_user_text: original user text for the transcript when callers
    rewrote message_text (e.g. directive-tap option values). The rewritten
    text still drives booking logic; the transcript keeps what the human
    actually sent. inbound_message_id: provider message id stored alongside.
    """
    # Empty input carries no intent: stay silent (no reply, no transcript,
    # no state change). Inbounds translate SILENT_NO_REPLY to reply=None.
    if not str(message_text or "").strip():
        return ActionPayload(message="", widget_directive="SILENT_NO_REPLY",
                             field_meta={"silent": True})
    graph_message_text = message_text
    session_id = chat_session.session_id
    _transcript_user_text = (
        transcript_user_text if transcript_user_text is not None else graph_message_text
    )
    _inbound_id = inbound_message_id
    existing_summary, existing_window = _load_runtime_context(chat_session)

    # Cancellation gate: deterministic pre-graph handling while a cancel flow is
    # active (lookup → OTP → execute). Runs before the resend fast-path below so
    # cancel-context OTP resends never leak into the booking flow.
    cancel_action = await cancel_flow.handle_active_cancel_flow(
        db, org, org_config, chat_session, message_text, background_tasks
    )
    if cancel_action is not None:
        return cancel_action

    # Post-booking gate FIRST (before decline/refusal/lookup/OTP fast-paths):
    # on a finished session (CONFIRMED / CANCELLED / EXPIRED) a 6-digit code
    # is never a booking OTP — it is chatter (or a re-sent old code) and must
    # reach the status-aware post-booking handler, never verify-and-continue
    # into a fresh booking. Cancel-flow OTP keeps working because the active
    # cancel gate above already returned for it.
    if chat_session.session_status in ("CONFIRMED", "CANCELLED", "EXPIRED"):
        return await _handle_post_booking_session(
            db, org, org_config, chat_session, message_text, background_tasks
        )

    # Booking decline: an explicit "don't want to book" exits gracefully
    # instead of being interrogated for fields. (Cancel turns already
    # returned above, so this never swallows a cancellation.)
    if decline_flow.is_no_booking_request(message_text):
        return await decline_flow.start_decline(
            db, org, chat_session, message_text, inbound_message_id=_inbound_id
        )
    # Off-topic cooldown: after a run of questions the assistant cannot help
    # with, pause the session briefly. Checked before anything else so the
    # message is answered without burning an LLM call or advancing the
    # booking, and so a customer mid-booking is told the wait plainly rather
    # than having their message silently swallowed.
    try:
        from app.engine import offtopic_cooldown as _cooldown

        _cd_remaining = _cooldown.cooldown_remaining(chat_session.collected_data)
    except Exception:
        _cooldown = None
        _cd_remaining = 0
    if _cd_remaining > 0:
        _cd_msg = (
            _cooldown.cooldown_message(_cd_remaining)
            if _cooldown
            else "You're still in cooldown. Please try again in a moment."
        )
        try:
            await _persist_transcript_best_effort(
                db,
                session_id=session_id,
                user_message=_transcript_user_text,
                user_external_id=_inbound_id,
                assistant_message=_cd_msg,
                directive="TEXT_INPUT",
                intent=chat_session.current_intent,
            )
        except Exception:
            pass
        return ActionPayload(
            message=_cd_msg,
            widget_directive="TEXT_INPUT",
            field_meta={"cooldown_remaining_seconds": _cd_remaining},
        )

    # Re-engagement clears a recorded decline (values or book-words).
    await decline_flow.maybe_reengage(db, chat_session, message_text)

    # Opening greeting: a customer whose FIRST message is their name ("Hi
    # Yash", "Amanpreet Kaur") was told "Hello! Could you please share your
    # full name?" -- the name they had just given was ignored and they were
    # asked for it again. Capture it, acknowledge them by name, and move on
    # to the next missing field.
    #
    # Placed after the cancel / post-booking / decline / cooldown gates so
    # none of those can be bypassed, and before the LLM so the salutation is
    # deterministic and cannot greet the customer by the wrong name.
    try:
        _opening_fds = order_field_definitions(
            parse_field_definitions(
                getattr(org_config, "field_definitions", None)
                or getattr(org_config, "required_fields", None)
            )
        )
        _greeting_action = await message_processor.maybe_greet_opening_name(
            db,
            org,
            org_config,
            chat_session,
            message_text,
            field_definitions=_opening_fds,
            inbound_message_id=_inbound_id,
            background_tasks=background_tasks,
        )
    except Exception as _greet_err:
        logger.warning("opening greeting failed, falling through: %s", _greet_err)
        _greeting_action = None
    if _greeting_action is not None:
        return _greeting_action

    # Field refusal: "no" / "don't want to share X" aimed at the current
    # missing field gets a soft re-ask of the SAME field — never a jump
    # ahead, never interrogation for something else.
    refusal_action = await decline_flow.maybe_handle_field_refusal(
        db, org, chat_session, message_text, inbound_message_id=_inbound_id
    )
    if refusal_action is not None:
        return refusal_action

    # Pending lookup completion: contact sent after a lookup request
    # finishes the check instead of feeding the booking flow.
    pending_action = await check_booking_flow.maybe_complete_pending_lookup(
        db, org, org_config, chat_session, message_text
    )
    if pending_action is not None:
        return pending_action

    # Fast-path: explicit OTP resend while awaiting verification.
    # The LLM is unreliable at routing a bare "resend", so handle it directly.
    if _is_resend_request(message_text) and _is_awaiting_otp(chat_session):
        return await _handle_graph_otp_resend(
            db, org, chat_session, message_text, background_tasks, org_config=org_config
        )

    # Fast-path: 6-digit OTP code sent as chat message (WhatsApp phone OTP
    # or email OTP). Verify deterministically before the LLM sees it — the
    # LLM hallucinates "no pending" otherwise and the code is lost.
    _stripped = str(message_text or "").strip()
    _is_six = bool(re.fullmatch(r"\d{6}", _stripped))
    _awaiting = _is_awaiting_otp(chat_session)
    _raw_p_log = getattr(org_config, "require_phone_otp", False)
    _phone_pending_log = bool((False if _raw_p_log is None else bool(_raw_p_log)) and not bool(getattr(chat_session, "phone_otp_verified", False)))
    if not _is_six and _awaiting:
        _code_match = re.search(r"(?<!\d)(\d{6})(?!\d)", _stripped)
        if _code_match:
            _stripped = _code_match.group(1)
            _is_six = True
    logger.info(f"OTP chat check: is_6digit={_is_six} awaiting={_awaiting} phone_pending={_phone_pending_log}")

    # Snapshot any OTP record that already existed BEFORE this turn. The send
    # happens later (when the customer confirms), so comparing this against the
    # record at the end of the turn tells us, deterministically, whether the
    # code was sent on THIS turn or on an earlier one.
    #
    # That distinction matters because the same "code pending" state needs two
    # opposite replies:
    #   sent on this turn  -> "I've sent a verification code to ..."
    #   sent earlier       -> the booking-assistant line + enter your code
    _otp_created_at_before: Optional[Any] = None
    try:
        import uuid as _uuid_snap
        from sqlalchemy.future import select as _sel_snap

        from app.models.otp import OTPRecord as _OTPSnap

        try:
            _sid_snap = _uuid_snap.UUID(str(session_id))
        except Exception:
            _sid_snap = session_id
        _snap_row = (
            await db.execute(
                _sel_snap(_OTPSnap.created_at).where(_OTPSnap.session_id == _sid_snap)
            )
        ).scalars().first()
        _otp_created_at_before = _snap_row
    except Exception:
        _otp_created_at_before = None
    if _is_six:
        # Determine which OTP channel is actually pending for this org/session.
        _raw_pp = getattr(org_config, "require_phone_otp", False)
        _raw_ep = getattr(org_config, "require_otp", True)
        _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
        # Was any OTP sent for this session? Consulted so a code turn is
        # NEVER skipped merely because the snapshot lost collected phone.
        _otp_was_sent = False
        try:
            _es0 = _active_effect_state(chat_session)
            _sk0 = _es0.get("otp_sent_keys")
            if isinstance(_sk0, list) and _sk0:
                _otp_was_sent = True
            else:
                from app.models.otp import OTPRecord as _OTPRec
                from sqlalchemy.future import select as _selrec

                _rec = (
                    await db.execute(
                        _selrec(_OTPRec.id)
                        .where(_OTPRec.session_id == session_id)
                        .limit(1)
                    )
                ).scalars().first()
                _otp_was_sent = _rec is not None
        except Exception:
            pass
        # Try the more likely channel first: phone for WhatsApp, email otherwise.
        # whatsapp_cloud (Meta Cloud API) is WhatsApp-class: same OTP routing.
        _channels = resolve_otp_verify_channels(
            is_whatsapp=_is_whatsapp,
            require_phone_otp=(False if _raw_pp is None else bool(_raw_pp)),
            require_otp=(True if _raw_ep is None else bool(_raw_ep)),
            phone_verified=bool(getattr(chat_session, "phone_otp_verified", False)),
            email_verified=bool(getattr(chat_session, "otp_verified", False)),
            collected_phone=str((chat_session.collected_data or {}).get("phone") or ""),
            otp_was_sent=_otp_was_sent,
        )
        if _channels:
            graph_message_text = "[verification code entered]"
            # Privacy redaction outranks the original-text override: a pasted
            # OTP must never reach the transcript (or Admin Inbox) in the clear,
            # even when the inbound handler supplied transcript_user_text.
            _transcript_user_text = "[verification code entered]"
        # Only attempt verify if at least one channel is pending.
        # Previous check required _is_awaiting_otp (sent_keys/last_sent_at) which
        # could be False due to state_data vs collected_data mismatch, causing
        # correct OTPs to fall through to the graph and re-trigger OTP send
        # (the loop in the screenshot). Any 6-digit code when OTP is pending
        # should be tried as verification.
        if _channels:
            _verified_channel = None
            for _ch in _channels:
                try:
                    if await otp_crud.verify_otp(db, session_id, code=_stripped, channel=_ch):
                        _verified_channel = _ch
                        break
                except ValueError:
                    # Too many attempts / cooldown — surface as invalid
                    pass
                except Exception:
                    pass
            if _verified_channel:
                # Reload the session row FIRST: verify_otp() records the
                # verified-context key (phone_otp_verified_key /
                # otp_verified_key) straight into the DB row, and this turn's
                # in-memory snapshot predates it. Without the reload,
                # _handle_booking_flow's context check sees a verified flag
                # with a missing key, revokes the verification, sends a new
                # code — and the user loops on OTP forever.
                try:
                    _fresh_session = await crud_chat.get_chat_session(db, session_id)
                    if _fresh_session is not None:
                        chat_session = _fresh_session
                except Exception:
                    pass
                # Mark verified in session so the graph sees it and proceeds to
                # booking in the same turn. Persist before graph invocation.
                if _verified_channel == "phone":
                    chat_session.phone_otp_verified = True
                    # Store the verified key OUTSIDE the guarded block below.
                    # That block ends in `except Exception: pass`, so anything
                    # raising inside it silently skipped the key write; the
                    # booking flow then read an empty key, judged the
                    # verification bogus, revoked it, and booked nothing.
                    _kp = str(
                        (getattr(chat_session, "collected_data", None) or {}).get("phone")
                        or ""
                    ).strip()
                    if _kp:
                        from app.engine.message_processor import (
                            _ensure_internal_state as _ensure_internal,
                        )

                        _ensure_internal(chat_session)[
                            "phone_otp_verified_key"
                        ] = f"otp_phone:{session_id}:{_kp}"
                    else:
                        logger.warning(
                            "OTP verified but no phone in collected_data; keys=%s",
                            (getattr(chat_session, "collected_data", None) or {}).keys(),
                        )
                    try:
                        _es = _active_effect_state(chat_session)
                        _sk = _es.get("otp_sent_keys")
                        # Backfill the verified destination BEFORE clearing keys:
                        # the otp_phone: key embeds the number the code went to.
                        # This makes "verified but missing phone" impossible.
                        if isinstance(_sk, list):
                            for _k in _sk:
                                _ks = str(_k or "")
                                if _ks.startswith("otp_phone:"):
                                    _parts = _ks.split(":")
                                    if len(_parts) >= 3 and _parts[2].strip():
                                        _cd = dict(chat_session.collected_data or {})
                                        chat_session.collected_data = apply_verified_phone(
                                            _cd, _parts[2].strip()
                                        )
                                    break
                        # Build the verified key from the session + phone we can
                        # actually see. _build_phone_otp_context_key() also
                        # embeds date/time, so it returned "" whenever the slot
                        # fields were absent, and the bare `except` below hid
                        # it. Nothing was ever stored, so the booking flow read
                        # an empty key, judged the verification bogus, revoked
                        # it, and no booking was ever created.
                        _verified_phone = str(
                            (getattr(chat_session, "collected_data", None) or {}).get("phone")
                            or ""
                        ).strip()
                        if not _verified_phone:
                            for _k in _sk or []:
                                _ks = str(_k or "")
                                if _ks.startswith("otp_phone:"):
                                    _pp = _ks.split(":")
                                    if len(_pp) >= 3 and _pp[2].strip():
                                        _verified_phone = _pp[2].strip()
                                        break
                        _verified_ctx = (
                            f"otp_phone:{session_id}:{_verified_phone}"
                            if _verified_phone
                            else ""
                        )
                        if _verified_ctx:
                            _ensure_internal_state(chat_session)[
                                "phone_otp_verified_key"
                            ] = _verified_ctx
                            logger.info(
                                "OTP verified key stored=%s",
                                _verified_ctx,
                            )
                        if isinstance(_sk, list):
                            # Clear phone OTP keys; keep email keys if any
                            _es["otp_sent_keys"] = [k for k in _sk if not str(k).startswith("otp_phone:")]
                            if not _es["otp_sent_keys"]:
                                _es.pop("otp_sent_keys", None)
                    except Exception:
                        pass
                else:
                    chat_session.otp_verified = True
                    # Clear the email OTP pending keys so _is_awaiting_otp flips
                    try:
                        _es = _active_effect_state(chat_session)
                        _es.pop("otp_sent_keys", None)
                    except Exception:
                        pass
                await crud_chat.update_chat_session(db, session_id, chat_session)
                # Fall through to graph — with verified flag set, it will
                # proceed to booking confirmation instead of re-asking for OTP.
            else:
                _recovered_verified = False
                try:
                    _later_session = await crud_chat.get_chat_session(db, session_id)
                    if _later_session is not None:
                        _later_channels = resolve_otp_verify_channels(
                            is_whatsapp=_is_whatsapp,
                            require_phone_otp=(False if _raw_pp is None else bool(_raw_pp)),
                            require_otp=(True if _raw_ep is None else bool(_raw_ep)),
                            phone_verified=bool(getattr(_later_session, "phone_otp_verified", False)),
                            email_verified=bool(getattr(_later_session, "otp_verified", False)),
                            collected_phone=str((getattr(_later_session, "collected_data", None) or {}).get("phone") or ""),
                            otp_was_sent=True,
                        )
                        if not _later_channels:
                            chat_session = _later_session
                            _recovered_verified = True
                            logger.info("otp verify race recovered: code already verified for session")
                except Exception:
                    pass
                if not _recovered_verified:
                    logger.warning("otp verify failed channels=%s session=%s", _channels, str(session_id)[:8])
                    _msg = "That code is incorrect or has expired. Please check the 6-digit code and try again, or say 'resend' to get a new code."
                    _field_meta: Dict[str, Any] = {"channel": _channels[0] if _channels else "phone"}
                    try:
                        _meta = await otp_crud.get_otp_policy_metadata(db, session_id, channel=_channels[0])
                        _field_meta.update(_meta)
                    except Exception:
                        pass
                    _action = ActionPayload(message=_msg, widget_directive="REQUIRE_OTP_MODAL", field_meta=_field_meta)
                    await _persist_transcript_best_effort(db, session_id=session_id, user_message=_transcript_user_text, user_external_id=_inbound_id, assistant_message=_msg, directive="REQUIRE_OTP_MODAL", intent=chat_session.current_intent)
                    return _action

    # NOTE: post-booking gate lives at the top of this function (before the
    # OTP fast-paths) so re-sent codes on finished sessions can never fork a
    # fresh booking. See above.

    # Convert org_config to a clean dictionary removing SQLAlchemy's internal state
    org_config_dict = {k: v for k, v in org_config.__dict__.items() if not k.startswith('_')}
    # Use business_name for RAG filtering (falls back to org.name if not set)
    org_config_dict["business_name"] = org_config_dict.get("business_name") or getattr(org, "name", None) or ""

    # 1. Prepare configuration for LangGraph (passed as configurable dict)
    config = {
        "configurable": {
            "thread_id": session_id,
            "session_id": session_id,
            "db": db,
            "org_id": str(org.id),
            "org_config": org_config_dict,
            # Optional: pass user's timezone if captured
            "user_offset_hours": 0 
        }
    }
    
    # 2. Invoke the graph with the new user message
    # If using memory saver, the checkpoint handles previous messages.
    logger.info(f"Invoking LangGraph for session {session_id}")
    prior_missing_fields = list(chat_session.missing_fields or [])
    inputs = {
        "messages": ([_window_item_to_message(item) for item in existing_window] + [HumanMessage(content=graph_message_text)])[-MAX_RUNTIME_CONTEXT_MESSAGES:],
        "summary": existing_summary,
        "session_id": session_id,
        "org_id": str(org.id),
        "org_config": org_config_dict,
        "collected_fields": _normalize_collected_data(chat_session.collected_data),
        "current_intent": str(chat_session.current_intent or "START_BOOKING"),
        "otp_verified": bool(chat_session.otp_verified),
        "phone_otp_verified": bool(getattr(chat_session, "phone_otp_verified", False)),
        "otp_required": False,
        "otp_pending": bool(
            (not chat_session.otp_verified)
            or (not getattr(chat_session, "phone_otp_verified", False))
        ),
        "otp_event_key": "",
        "otp_code": "",
        "validation_errors": {},
        "validated_field_metadata": {},
        "knowledge_answer": False,
        "context_window": [],
        "context_window_size": 0,
        "context_compacted": False,
        "date_reset_required": False,
        "fallback_reason": "",
        "fallback_next_step": "",
        "schedule_answer": {},
        "slot_unavailable": False,
        "refreshed_slots": [],
        "optional_skips": [],
        "pending_conflict": {},
        "follow_up_fields": list(chat_session.missing_fields or []),
        "captured_deltas": {},
        "merge_diagnostics": {},
    }
    
    # The agent runs asynchronously across its nodes.
    # We use await ainvoke because our tool checks async slot_engine
    try:
        result = await asyncio.wait_for(booking_agent.ainvoke(inputs, config=config), timeout=GRAPH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error("booking_agent ainvoke failed: timeout")
        if raise_on_timeout:
            raise
        action = ActionPayload(
            message="I'm sorry, I encountered an internal error. Please try again.",
            widget_directive="TEXT_INPUT",
        )
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=_transcript_user_text, user_external_id=_inbound_id,
            assistant_message=action.message,
            directive=action.widget_directive or "TEXT_INPUT",
            intent=chat_session.current_intent,
        )
        return action
    except Exception as _graph_exc:
        logger.error("booking_agent ainvoke failed", exc_info=True)
        action = ActionPayload(
            message="I'm sorry, I encountered an internal error. Please try again.",
            widget_directive="TEXT_INPUT"
        )
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=_transcript_user_text, user_external_id=_inbound_id,
            assistant_message=action.message,
            directive=action.widget_directive or "TEXT_INPUT",
            intent=chat_session.current_intent,
        )
        return action

    # Extract the final AI message from the agent's updated state
    messages = result.get("messages", [])
    
    if not messages:
        action = ActionPayload(
            message="I'm sorry, I encountered an internal error. Please try again.",
            widget_directive="TEXT_INPUT"
        )
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=_transcript_user_text, user_external_id=_inbound_id,
            assistant_message=action.message,
            directive=action.widget_directive or "TEXT_INPUT",
            intent=chat_session.current_intent,
        )
        return action
        
    last_message = messages[-1]
    assistant_message, structured_assistant_payload = _extract_user_visible_message(
        getattr(last_message, "content", "")
    )

    # Cancellation entry: if the LLM classified (or keywords indicate) a cancel
    # request and no cancel flow is active, branch into the deterministic
    # cancellation flow instead of applying booking state.
    llm_intent = str((structured_assistant_payload or {}).get("user_intent") or "")
    if cancel_flow.is_cancel_intent(message_text, llm_user_intent=llm_intent):
        action = await cancel_flow.start_cancel_flow(db, chat_session, message_text)
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=_transcript_user_text, user_external_id=_inbound_id,
            assistant_message=action.message,
            directive=action.widget_directive or "TEXT_INPUT",
            intent="CANCEL_BOOKING",
        )
        return action

    # Existing-booking lookup: driven SOLELY by the LLM's classification
    # (user_intent == check_booking / CHECK_BOOKING). No keyword/regex
    # inspection of message_text here — the model reads the actual meaning,
    # so paraphrases ("do i have one?", "show my details", ...) all work.
    # Booking progress is preserved inside the handler.
    _normalized_intent = llm_intent.strip().lower().replace(" ", "_")
    if _normalized_intent in ("check_booking", "checkbooking", "lookup_booking"):
        action = await check_booking_flow.handle_check_booking(
            db, org, org_config, chat_session
        )
        return action
    
    existing_collected_data = (
        dict(chat_session.collected_data) if isinstance(chat_session.collected_data, dict) else {}
    )
    collected_fields = result.get("collected_fields")
    validated_field_metadata = result.get("validated_field_metadata")
    if isinstance(collected_fields, dict):
        merged_collected_data = dict(existing_collected_data)
        merged_collected_data.update(collected_fields)
        if isinstance(validated_field_metadata, dict):
            phone_meta = validated_field_metadata.get("phone")
            if isinstance(phone_meta, dict):
                country_code = phone_meta.get("country_code")
                raw_input = phone_meta.get("raw_input")
                if isinstance(country_code, str) and country_code.strip():
                    merged_collected_data["phone_country_code"] = country_code.strip().upper()
                if isinstance(raw_input, str) and raw_input.strip():
                    merged_collected_data["phone_raw_input"] = raw_input.strip()
        chat_session.collected_data = merged_collected_data
    # Providing real booking data re-engages a declined session.
    if isinstance(result.get("captured_deltas"), dict) and result["captured_deltas"]:
        await decline_flow.clear_decline(db, chat_session)
    follow_up_fields = result.get("follow_up_fields")
    if result.get("slot_unavailable"):
        existing_follow_ups = list(follow_up_fields) if isinstance(follow_up_fields, list) else []
        refreshed_slots = result.get("refreshed_slots")
        has_refreshed_slots = isinstance(refreshed_slots, list) and len(refreshed_slots) > 0
        fallback_reason = str(result.get("fallback_reason") or "").strip() or "no_slots_available_for_date"
        fallback_next_step = str(result.get("fallback_next_step") or "").strip() or "pick_another_date"

        if has_refreshed_slots:
            if "time" not in existing_follow_ups:
                existing_follow_ups.insert(0, "time")
        else:
            for stale_key in ("selected_slot_id", "selected_slot_start_utc", "time", "date"):
                if isinstance(chat_session.collected_data, dict):
                    chat_session.collected_data.pop(stale_key, None)
            existing_follow_ups = ["date"]
            result["date_reset_required"] = True
            result["fallback_reason"] = fallback_reason
            result["fallback_next_step"] = fallback_next_step

        follow_up_fields = existing_follow_ups
    if isinstance(follow_up_fields, list):
        follow_up_fields = _enforce_runtime_contact_requirements(
            follow_up_fields,
            dict(chat_session.collected_data or {}),
            org_config_dict,
        )
    _graph_verdict = result.get("current_intent")
    current_intent = str(
        _graph_verdict or chat_session.current_intent or "START_BOOKING"
    )
    if isinstance(follow_up_fields, list) and not follow_up_fields:
        # LLM-primary: the graph already classified this turn (current_intent).
        # Keyword fallbacks below fire ONLY when the graph was indecisive —
        # a decisive graph verdict (CONFIRM/EDIT/CANCEL/CHECK) always stands.
        # No verdict at all (silent/partial result) keeps the legacy keyword
        # behavior — the carried-over session intent is stale, not a decision.
        _graph_undecided = (not _graph_verdict) or current_intent in ("PROVIDE_DATA", "START_BOOKING", "ASK_QUESTION")
        user_msg_lower = message_text.lower().strip()
        _is_bare_edit = user_msg_lower in {"change", "edit", "modify", "update", "change details", "edit details"}
        _has_edit_signal = any(kw in user_msg_lower for kw in ("change", "edit", "modify", "update"))
        if _graph_undecided and (message_processor._is_edit_details_request(user_msg_lower) or _is_bare_edit):
            # Bare "change" without a field: ask which field with a picker,
            # don't fall through to an assumed "name" ask.
            if _is_bare_edit and not message_processor._is_edit_details_request(user_msg_lower):
                from app.schemas.agent import ActionPayload as _AP
                _msg_pick = "Sure, which field would you like to change? You can say name, email, phone, date, or time."
                _ap_pick = _AP(message=_msg_pick, widget_directive="TEXT_INPUT", field_meta={"name": "edit_choice", "type": "text", "label": "Field to change"})
                await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
                await _persist_transcript_best_effort(db, session_id=chat_session.session_id, user_message=message_text, assistant_message=_msg_pick, directive="TEXT_INPUT", intent="EDIT_DATA")
                return _ap_pick
            current_intent = "EDIT_DATA"
        else:
            # CONFIRM must always work on finish screen — even when graph was
            # "decisive" as ASK_QUESTION on a short token. Pure
            # confirm words force CONFIRM unless it's explicitly an edit turn.
            try:
                from app.engine.time_flex import is_confirm_message as _is_confirm_msg
                _confirmed = _is_confirm_msg(message_text)
            except Exception:
                _confirmed = any(kw in user_msg_lower for kw in message_processor.CONFIRM_KEYWORDS)
            _explicit_confirm = bool(
                re.search(
                    r"\b(?:yes|yep|yup|ok|okay|sure|confirm(?:ed|ation)?|proceed|book it|go ahead|done)\b",
                    user_msg_lower,
                )
            )
            _is_pure_confirm = user_msg_lower.strip() in {
                "confirm", "yes", "yep", "yup", "ok", "okay", "sure", "proceed",
                "book it", "go ahead", "done", "confirmed",
            } or (_confirmed and len(user_msg_lower.strip().split()) <= 2) or _explicit_confirm
            _is_edit_mixed = _has_edit_signal and current_intent == "EDIT_DATA"
            if not follow_up_fields and (_confirmed or _is_pure_confirm):
                if _graph_undecided or _is_pure_confirm:
                    if not _is_edit_mixed and current_intent != "CANCEL_BOOKING":
                        current_intent = "CONFIRM_BOOKING"
                elif _is_pure_confirm and current_intent not in ("CANCEL_BOOKING", "EDIT_DATA"):
                    current_intent = "CONFIRM_BOOKING"
    chat_session.current_intent = current_intent
    if current_intent == "CONFIRM_BOOKING" and not follow_up_fields:
        # current_intent is carried across turns, so a CONFIRM_BOOKING left
        # over from a previous turn would otherwise authorise a booking on
        # whatever the customer says next. The booking must be authorised by
        # a confirmation in THIS turn.
        if _is_user_confirmation(message_text):
            directive = "PROCESS_BOOKING"

    if isinstance(follow_up_fields, list):
        chat_session.missing_fields = follow_up_fields

    # Free-text times ("3 pm", "3pm", "3:00 pm") are valid on text-only
    # channels (WhatsApp). The old code discarded ANY free-text time without
    # a button-tap `selected_slot_id`, causing the "Select your preferred
    # time" loop from the screenshot. Only enforce button-tap on web where
    # slot buttons actually render; on WhatsApp canonicalize to the slot label.
    _is_web = str(getattr(chat_session, "channel", "") or "").lower() == "web"
    # Detect fallback recovery (LLM outage) — the graph returns a
    # "temporary issue" message when it extracted time via regex. In that
    # case don't discard the valid time; otherwise the web slot-button guard
    # would loop and the outage tests would fail.
    _is_fallback_recovery = False
    try:
        _msgs = result.get("messages") or []
        if _msgs:
            _last = _msgs[-1]
            _txt = getattr(_last, "content", "") or str(_last)
            if isinstance(_txt, str) and "temporary issue" in _txt.lower():
                _is_fallback_recovery = True
    except Exception:
        pass
    manual_time_requires_slot_selection = (
        _is_web
        and "time" in prior_missing_fields
        and "time" not in (chat_session.missing_fields or [])
        and not str(chat_session.collected_data.get("selected_slot_id") or "").strip()
        and not _is_fallback_recovery
    )
    if not manual_time_requires_slot_selection and not _is_web:
        # Canonicalize WhatsApp free-text time to "3:00 PM" slot-label form
        # so downstream lock/summary/display all agree.
        try:
            from app.engine.time_flex import parse_flexible_time as _parse_flex

            _raw_time = str((chat_session.collected_data or {}).get("time") or "") or str(
                message_text or ""
            )
            _parsed_t = _parse_flex(_raw_time) or _parse_flex(str(message_text or ""))
            if _parsed_t is not None and "time" not in (chat_session.missing_fields or []):
                from app.engine.slot_engine import format_slot_time as _fmt_slot

                chat_session.collected_data["time"] = _fmt_slot(_parsed_t)
        except Exception:
            pass
    if manual_time_requires_slot_selection:
        chat_session.collected_data.pop("time", None)
        chat_session.collected_data.pop("locked_slot_id", None)
        chat_session.collected_data.pop("locked_slot_start_utc", None)
        chat_session.missing_fields = [
            "time",
            *[field for field in (chat_session.missing_fields or []) if field != "time"],
        ]
        follow_up_fields = list(chat_session.missing_fields)

    # ── Slot availability check (reject unavailable times) ──
    # If time was just collected via free-text (no slot button), verify it is
    # actually an available slot for the selected date. Previously any valid
    # time (e to 10 AM when only 9,11,1,3 are open) was accepted and booking
    # proceeded to OTP even though the slot doesn't exist — the second bug.
    # Skip if time was selected via a slot button (selected_slot_id set)
    # or if no schedule/inventory (tests with no BusinessSchedule).
    if (
        "time" in prior_missing_fields
        and "time" not in (chat_session.missing_fields or [])
        and not str(chat_session.collected_data.get("selected_slot_id") or "").strip()
    ):
        try:
            _time_raw_av = str(chat_session.collected_data.get("time") or "").strip()
            _date_raw_av = str(chat_session.collected_data.get("date") or "").strip()
            if _time_raw_av and _date_raw_av:
                from app.engine.time_flex import parse_flexible_time as _pfl_av
                from app.engine.slot_engine import compute_slot_inventory as _csi_av, format_slot_time as _fmt_av, apply_scheduling_policy_to_inventory as _apsi_av
                from app.engine.scheduling_policy import resolve_effective_timezone_policy as _retzp_av
                from dateutil.parser import parse as _dp_av
                _parsed_t_av = _pfl_av(_time_raw_av) or _pfl_av(message_text or "")
                # Also try direct parse of collected time if flexible fails (e.g. "13:30:00")
                if _parsed_t_av is None:
                    try:
                        _parsed_t_av = _dp_av(_time_raw_av).time()
                    except Exception:
                        _parsed_t_av = None
                _parsed_d_av = _dp_av(_date_raw_av).date()
                if _parsed_t_av is not None:
                    _inv_av, _status_av = await _csi_av(db, org.id, _parsed_d_av, org_config=org_config)
                    try:
                        _sched_res_av = await db.execute(select(BusinessSchedule).where(BusinessSchedule.org_id == org.id))
                        _sched_av = _sched_res_av.scalars().first()
                        _org_tz_av = str((_sched_av.timezone if _sched_av and _sched_av.timezone else None) or getattr(org_config, "timezone", None) or "UTC")
                    except Exception:
                        logger.warning("slot timezone resolve failed (availability check)", exc_info=True)
                        _org_tz_av = str(getattr(org_config, "timezone", None) or "UTC")
                    _tz_res_av = _retzp_av(
                        requested_tz=chat_session.collected_data.get("user_timezone"),
                        org_tz=_org_tz_av,
                        requested_source=chat_session.collected_data.get("timezone_source"),
                    )
                    _eff_tz_av = str(_tz_res_av.get("effective_timezone") or _org_tz_av)
                    _inv_av, _status_av, _ = _apsi_av(
                        _inv_av, _parsed_d_av, base_status=_status_av, effective_timezone=_eff_tz_av, **_slot_policy_kwargs(org_config)
                    )
                    try:
                        from zoneinfo import ZoneInfo as _ZI_av
                        _tz_obj_av = _ZI_av(_eff_tz_av)
                    except Exception:
                        import datetime as _dt_av
                        _tz_obj_av = _dt_av.timezone.utc
                    _available_labels_av = []
                    for _it_av in _inv_av:
                        if not _it_av.get("available"):
                            continue
                        _s_av = _it_av.get("start_utc")
                        if _s_av is None:
                            continue
                        try:
                            if getattr(_s_av, "tzinfo", None) is None:
                                import datetime as _dt2_av
                                _s_av = _s_av.replace(tzinfo=_dt2_av.timezone.utc)
                            _local_t_av = _s_av.astimezone(_tz_obj_av).time()
                            _available_labels_av.append(_fmt_av(_local_t_av))
                        except Exception:
                            continue
                    if _available_labels_av:
                        _candidate_label_av = _fmt_av(_parsed_t_av)
                        if _candidate_label_av not in _available_labels_av:
                            logger.info(f"slot_not_available: requested={_candidate_label_av} available={_available_labels_av[:5]} date={_parsed_d_av}")
                            chat_session.collected_data.pop("time", None)
                            chat_session.collected_data.pop("locked_slot_id", None)
                            chat_session.collected_data.pop("locked_slot_start_utc", None)
                            chat_session.collected_data.pop("selected_slot_id", None)
                            chat_session.missing_fields = ["time"] + [f for f in (chat_session.missing_fields or []) if f != "time"]
                            follow_up_fields = list(chat_session.missing_fields)
                            # Store for next turn's rendering - will be recomputed, but set a hint
                            # The next turn will show "That slot is not available..." via the slot_unavailable path
                            # For now, we just ensure time is missing so OTP doesn't trigger
        except Exception as _slot_av_err:
            logger.warning(f"slot availability check failed: {_slot_av_err}", exc_info=True)

    # ── Change collected field override (deterministic Python-side) ──
    # When the user asks to change a field that was already collected, re-ask
    # exactly that field instead of emitting the next missing field's widget.
    # Skipped when the same turn already captured a fresh value for the field.
    CHANGE_COLLECTED_FIELD_KEYWORDS = {
        "name": [
            "change name",
            "change my name",
            "update name",
            "wrong name",
        ],
        "email": [
            "change email",
            "change my email",
            "update email",
            "wrong email",
            "different email",
        ],
        "phone": [
            "change phone",
            "change my phone",
            "update phone",
            "wrong phone",
            "change number",
            "change my number",
            "different number",
        ],
        "date": [
            "change date",
            "change the date",
            "change my date",
            "different date",
            "another date",
            "wrong date",
            "pick another date",
            "choose another date",
            "switch date",
            "update date",
            "new date",
        ],
        "time": [
            "change time",
            "change my time",
            "change the time",
            "wrong time",
            "update time",
            "different time",
        ],
    }
    missing_set_override = set(chat_session.missing_fields or [])
    changed_field = None
    # Prefer graph's reopen_fields if provided (structural, not keyword)
    _reopen_candidate = None
    if isinstance(result, dict) and isinstance(result.get("reopen_fields"), list) and result.get("reopen_fields"):
        _first = result.get("reopen_fields")[0]
        if isinstance(_first, str) and _first.strip():
            _reopen_candidate = _first.strip().lower()
    if not _reopen_candidate and isinstance(structured_assistant_payload, dict) and isinstance(structured_assistant_payload.get("reopen_fields"), list) and structured_assistant_payload.get("reopen_fields"):
        _first2 = structured_assistant_payload.get("reopen_fields")[0]
        if isinstance(_first2, str) and _first2.strip():
            _reopen_candidate = _first2.strip().lower()
    if _reopen_candidate and _reopen_candidate in CHANGE_COLLECTED_FIELD_KEYWORDS and _reopen_candidate not in missing_set_override and _reopen_candidate in (chat_session.collected_data or {}):
        changed_field = _reopen_candidate
    elif re.search(r"\b(don'?t|do not|dont|didn'?t|not|never|no need)\b", message_text.lower()):
        changed_field = None
    else:
        for field, kws in CHANGE_COLLECTED_FIELD_KEYWORDS.items():
            if field in missing_set_override:
                continue
            if field not in (chat_session.collected_data or {}):
                continue
            if any(kw in message_text.lower() for kw in kws):
                changed_field = field
                break
    if changed_field:
        chat_session.collected_data.pop(changed_field, None)
        if changed_field in ("date", "time"):
            chat_session.collected_data.pop("selected_slot_id", None)
            chat_session.collected_data.pop("selected_slot_start_utc", None)
            chat_session.collected_data.pop("locked_slot_id", None)
            chat_session.collected_data.pop("locked_slot_start_utc", None)
        if changed_field == "date":
            chat_session.collected_data.pop("time", None)
        follow_up_fields = [changed_field] + [
            f for f in follow_up_fields if f != changed_field
        ]
        chat_session.missing_fields = list(follow_up_fields)

    await crud_chat.update_chat_session(db, session_id, chat_session)
    session_state_dirty = False

    async def _write_checkpoint(sid: str, checkpoint: Dict[str, Any]) -> None:
        await crud_chat.save_session_checkpoint(db, sid, checkpoint)
        if isinstance(chat_session.collected_data, dict):
            chat_session.collected_data[crud_chat.RUNTIME_CHECKPOINT_KEY] = checkpoint

    checkpoint_manager = CheckpointManager(write_checkpoint=_write_checkpoint)
    context_window = _normalize_context_window(result.get("context_window"))
    summary = str(result.get("summary", "") or "")
    # FIX: prevent nested duplication of runtime_checkpoint inside collected_data
    _clean_collected = dict(chat_session.collected_data or {})
    _clean_collected.pop(crud_chat.RUNTIME_CHECKPOINT_KEY, None)
    checkpoint_state: Dict[str, Any] = {
        "collected_data": _clean_collected,
        "missing_fields": list(chat_session.missing_fields or []),
        "current_intent": chat_session.current_intent,
        "session_status": chat_session.session_status,
        "otp_verified": chat_session.otp_verified,
        "pending_cancel": chat_session.pending_cancel,
        "summary": summary,
        "context_window": context_window,
        "context_window_size": len(context_window),
    }
    captured_deltas = result.get("captured_deltas", {})
    event_type = "field_capture" if captured_deltas else "confirmation"
    await checkpoint_manager.record_event(session_id, event_type, checkpoint_state)

    field_meta = {
        "captured_deltas": captured_deltas,
        "follow_up_fields": follow_up_fields if isinstance(follow_up_fields, list) else [],
        "merge_diagnostics": result.get("merge_diagnostics", {}),
        "context": {
            "window_size": len(context_window),
            "summary_present": bool(summary),
        },
    }
    if manual_time_requires_slot_selection:
        field_meta["validation_error"] = "Please choose a time using the slot buttons below."
        field_meta["directive_retry"] = "REQUIRE_TIME_SLOT_GRID"
    if structured_assistant_payload:
        field_meta["llm_structured_response"] = {
            key: value
            for key, value in structured_assistant_payload.items()
            if key != "response_text"
        }
    if recovery_metadata:
        field_meta["recovery"] = recovery_metadata

    field_definitions = _resolve_field_definitions_snapshot(chat_session, org_config_dict)
    fd_map = {fd.name: fd for fd in field_definitions}
    # Hybrid meeting type selection before booking
    _collected = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
    _need_meeting_type = False
    try:
        from app.models.config import MeetingMode as _MM
        from app.engine.message_processor import _normalize_meeting_mode as _normM
        _m = _normM(getattr(org_config, "meeting_mode", None))
        _allow = bool(getattr(org_config, "allow_hybrid_selection", False))
        if not field_meta.get("follow_up_fields") and not _collected.get("meeting_type") and _m == _MM.HYBRID and _allow:
            # Check if user just sent Online/Offline
            _msg_lower = (message_text or "").strip().lower()
            if _msg_lower in ("online", "offline"):
                _collected["meeting_type"] = "ONLINE" if _msg_lower == "online" else "OFFLINE"
                chat_session.collected_data = _collected
                await crud_chat.update_chat_session(db, session_id, chat_session)
            else:
                _need_meeting_type = True
    except Exception:
        pass
    if _need_meeting_type:
        directive = "REQUIRE_SELECT"
        field_meta.update({
            "name": "meeting_type",
            "type": "select",
            "label": "Meeting Type",
            "options": ["Online", "Offline"],
            "description": "How would you like to meet?",
            "icon": "💻",
        })
        next_action = {"directive": directive, "field_meta": dict(field_meta)}
        directive_meta = dict(field_meta)
    else:
        next_action = StateEvaluator.determine_next_action(
            field_meta.get("follow_up_fields", []),
            current_intent,
            field_definitions=field_definitions,
        )
        directive = str(next_action.get("directive") or "TEXT_INPUT")
        directive_meta = next_action.get("field_meta")
    # DIAGNOSTIC for WhatsApp time-slot directive (as requested, no code change beyond logging)
    try:
        _diag_state_data = getattr(chat_session, "collected_data", {}) or {}
        _diag_active = _diag_state_data.get("active_directive_id") if isinstance(_diag_state_data, dict) else None
        # Also check state_data via ChatSessionState mapping
        _diag_missing = getattr(chat_session, "missing_fields", None)
        _diag_date = _diag_state_data.get("date") if isinstance(_diag_state_data, dict) else None
        logger.info(
            f"DIRECTIVE_DIAG _is_whatsapp={str(getattr(chat_session, 'channel', '')).lower() in ('whatsapp', 'whatsapp_cloud')} directive={directive} missing_fields={_diag_missing} "
            f"directive_meta={str(directive_meta)[:300] if directive_meta else None} "
            f"options_count={len(directive_meta.get('options') or []) if isinstance(directive_meta, dict) and directive_meta.get('options') else 0} "
            f"directive_type_map={directive} -> { {'REQUIRE_DATE_PICKER': 'SELECT_DATE', 'REQUIRE_TIME_SLOT_GRID': 'SELECT_SLOT', 'REQUIRE_SELECT': 'SELECT_SLOT', 'SELECT_DROPDOWN': 'SELECT_SLOT', 'REQUEST_CONFIRMATION': 'CONFIRM_BOOKING'}.get(directive, 'UNKNOWN')} "
            f"booking_date={_diag_date} active_directive_id={str(_diag_active)[:8] if _diag_active else None} state_data_keys={list(_diag_state_data.keys())[:6] if isinstance(_diag_state_data, dict) else 'not_dict'}"
        )
    except Exception as _diag_e:
        logger.warning(f"DIRECTIVE_DIAG failed: {_diag_e}")
    # WhatsApp BookingDirective — create persisted directive for structured UI
    # (WhatsApp-only, behind no flag for now as requested — widget keeps its own path)
    # whatsapp_cloud (Meta Cloud API) is WhatsApp-class: same directives/IDs/handler.
    _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
    if _is_whatsapp and directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID", "REQUIRE_SELECT", "SELECT_DROPDOWN", "REQUEST_CONFIRMATION", "BOOKING_SUMMARY_CARD"):
        try:
            from app.engine.booking_directive.service import DirectiveType as _DT

            _type_map = {
                "REQUIRE_DATE_PICKER": _DT.SELECT_DATE,
                "REQUIRE_TIME_SLOT_GRID": _DT.SELECT_SLOT,
                "REQUIRE_SELECT": _DT.SELECT_SLOT,
                "SELECT_DROPDOWN": _DT.SELECT_SLOT,
                "REQUEST_CONFIRMATION": _DT.CONFIRM_BOOKING,
                "BOOKING_SUMMARY_CARD": _DT.CONFIRM_BOOKING,
            }
            _dtype = _type_map.get(directive)
            if _dtype:
                _opts = []
                _raw = directive_meta.get("options") if isinstance(directive_meta, dict) else []
                # If raw is empty for SELECT_DATE/SLOT, generate authoritatively so directive is never skipped
                if not _raw and _dtype in (_DT.SELECT_DATE, _DT.SELECT_SLOT):
                    try:
                        if _dtype == _DT.SELECT_DATE:
                            from app.engine.message_processor import (
                                _format_available_dates_for_prompt,
                                _upcoming_date_options,
                            )

                            _tz = str(getattr(org_config, "timezone", None) or "UTC")
                            from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                            _max = (await _resolve_horizon(db, org.id, org_config))[0]
                            _gen = _upcoming_date_options(_tz, _max)
                            _raw = _gen if _gen else []
                            # attach helpful description with format + availability
                            if _raw and not directive_meta.get("description"):
                                try:
                                    from app.engine.message_processor import (
                                        fetch_closed_weekdays as _fetch_closed,
                                    )

                                    _closed = await _fetch_closed(db, org.id)
                                    directive_meta["description"] = (
                                        _format_available_dates_for_prompt(
                                            _tz, _max,
                                            count=min(7, len(_raw)),
                                            closed_weekdays=_closed,
                                        )
                                    )
                                except Exception:
                                    pass
                        elif _dtype == _DT.SELECT_SLOT:
                            # Generate real slots for the current booking date.
                            # collected_data is merged from the graph's output
                            # before this block runs, so it always holds the
                            # date the customer most recently gave -- including
                            # on the very turn they gave it. directive_meta may
                            # still carry the previous turn's target, and using
                            # that showed slots for the OLD date after the
                            # customer picked a new one. Production: "28
                            # September 2027" was stored correctly while the
                            # reply listed slots for 2026-10-02.
                            _target = (chat_session.collected_data or {}).get("date")
                            if not _target and isinstance(directive_meta, dict):
                                _target = directive_meta.get("target_date") or directive_meta.get("date")
                            if _target:
                                from dateutil.parser import parse as _parse
                                from app.engine.slot_engine import compute_slot_inventory, format_slot_time
                                from app.engine.scheduling_policy import resolve_effective_timezone_policy

                                _target_date = _parse(str(_target)).date()
                                _org_cfg = org_config
                                try:
                                    _sched_res = await db.execute(select(BusinessSchedule).where(BusinessSchedule.org_id == org.id))
                                    _sched = _sched_res.scalars().first()
                                    _tz_name = str((_sched.timezone if _sched and _sched.timezone else None) or getattr(_org_cfg, "timezone", None) or "UTC")
                                except Exception:
                                    logger.warning("slot timezone resolve failed (whatsapp slot)", exc_info=True)
                                    _tz_name = str(getattr(_org_cfg, "timezone", None) or "UTC")
                                _inv, _stat = await compute_slot_inventory(db, org.id, _target_date, org_config=_org_cfg)
                                _tz_res = resolve_effective_timezone_policy(
                                    requested_tz=(chat_session.collected_data or {}).get("user_timezone"),
                                    org_tz=_tz_name,
                                    requested_source=(chat_session.collected_data or {}).get("timezone_source"),
                                )
                                from app.engine.slot_engine import apply_scheduling_policy_to_inventory

                                _inv, _stat, _ = apply_scheduling_policy_to_inventory(
                                    _inv, _target_date, base_status=_stat, effective_timezone=str(_tz_res.get("effective_timezone") or _tz_name), **_slot_policy_kwargs(org_config)
                                )
                                try:
                                    from zoneinfo import ZoneInfo
                                except ImportError:
                                    from backports.zoneinfo import ZoneInfo
                                try:
                                    _tz_obj = ZoneInfo(str(_tz_res.get("effective_timezone") or _tz_name))
                                except Exception:
                                    import datetime as _dt

                                    _tz_obj = _dt.timezone.utc
                                _slot_labels = []
                                for _it in _inv:
                                    if not _it.get("available"):
                                        continue
                                    _s = _it.get("start_utc")
                                    if _s is None:
                                        continue
                                    try:
                                        if getattr(_s, "tzinfo", None) is None:
                                            import datetime as _dt2

                                            _s = _s.replace(tzinfo=_dt2.timezone.utc)
                                        _slot_labels.append(format_slot_time(_s.astimezone(_tz_obj).time()))
                                    except Exception:
                                        continue
                                    if len(_slot_labels) >= 10:
                                        break
                                _raw = _slot_labels
                                # WhatsApp text-only guardrail: when user selects date, show all available slots as plain messages (no directive)
                                # User explicitly requested: "we are not using any directives for our whatsapp bookings"
                                if _is_whatsapp and _dtype == _DT.SELECT_SLOT:
                                    # Build plain-text slots list for WhatsApp (no interactive)
                                    if _slot_labels:
                                        slots_text = "\n".join([f"• {label}" for label in _slot_labels])
                                        # If the customer already replied with a time
                                        # that is not on this list, say so plainly
                                        # instead of silently re-listing the same
                                        # options as if they had said nothing.
                                        _rejected = _unmatched_time_choice(
                                            message_text, _slot_labels
                                        )
                                        # If this turn is not a time attempt at all
                                        # (a question, chatter, gibberish), the
                                        # graph above has already answered it --
                                        # re-rendering the grid here would discard
                                        # that answer and trap the customer in a
                                        # loop of identical slot lists.
                                        # Production: six turns of the same list
                                        # for "do you have parking?" and friends.
                                        _is_time_attempt = bool(_rejected)
                                        try:
                                            from app.engine.validators import (
                                                DataValidator as _time_val,
                                            )
                                            _tnorm, _ = _time_val._validate_time(
                                                str(message_text or "")
                                            )
                                            _is_time_attempt = _is_time_attempt or bool(_tnorm)
                                        except Exception:
                                            pass
                                        # A turn that PROVIDED the date must still
                                        # render its slots, even though the
                                        # message is not a time. Only a turn
                                        # that provided nothing (a question,
                                        # chatter) keeps the graph's answer.
                                        _provided_this_turn = False
                                        try:
                                            from dateutil.parser import (
                                                parse as _this_turn_parse,
                                            )
                                            _turn_date = _this_turn_parse(
                                                str(message_text or "")
                                            ).date().isoformat()
                                            _provided_this_turn = bool(
                                                _turn_date
                                                and str(_target or "").strip()
                                                and _turn_date == str(_target).strip()[:10]
                                            )
                                        except Exception:
                                            pass
                                        if (
                                            not _is_time_attempt
                                            and not _provided_this_turn
                                            and str(
                                            getattr(chat_session, "current_intent", "")
                                        ) in ("ASK_QUESTION", "PROVIDE_DATA")
                                        ):
                                            # Leave the graph's answer intact.
                                            pass
                                        else:
                                            _prefix = (
                                                f"{_rejected} is not a valid time. "
                                                if _rejected
                                                else ""
                                            )
                                            assistant_message = (
                                            f"{_prefix}Available slots for {str(_target)}:\n"
                                            f"{slots_text}\n\nPlease reply with your "
                                            f"preferred time (e.g., {_slot_labels[0]})"
                                        )
                                        # Remember what was offered so a typed
                                        # time can be checked against THIS list
                                        # on the next turn, when the slot
                                        # options are not being re-rendered.
                                        field_meta["available_slots"] = _slot_labels
                                    else:
                                        assistant_message = f"No available slots for {str(_target)}. Please try another date (e.g., tomorrow)."
                                    directive = "TEXT_INPUT"
                                    field_meta.update(
                                        {
                                            "name": "time",
                                            "type": "time",
                                            "label": "Preferred Time",
                                            "available_slots": _slot_labels,
                                            "target_date": str(_target),
                                            "whatsapp_text_slots": True,
                                        }
                                    )
                                    # Skip directive creation - text only
                                    _raw = []
                                    # Continue to bypass directive creation; will return as TEXT_INPUT below
                                # Also set time prompt in requested format: hours + duration, with closure/exception
                                if _slot_labels and not directive_meta.get("description"):
                                    try:
                                        from app.engine.message_processor import _format_time_prompt_for_whatsapp
                                        _weekly3 = None
                                        _excs3 = None
                                        try:
                                            from app.models.booking import ScheduleDay as _SD3b, ScheduleException as _SE3b
                                            from sqlalchemy.future import select as _sel3b
                                            _sd3b_res = await db.execute(_sel3b(_SD3b).where(_SD3b.org_id == org.id))
                                            _sd3b = _sd3b_res.scalars().all()
                                            _weekly3 = [
                                                {"day_of_week": r.day_of_week, "is_active": r.is_active, "open_time": r.open_time, "close_time": r.close_time}
                                                for r in _sd3b
                                            ]
                                            _se3b_res = await db.execute(_sel3b(_SE3b).where(_SE3b.org_id == org.id))
                                            _se3b = _se3b_res.scalars().all()
                                            _excs3 = [
                                                {"exception_date": r.exception_date, "exception_type": str(r.exception_type), "custom_open": r.custom_open, "custom_close": r.custom_close}
                                                for r in _se3b
                                            ]
                                        except Exception:
                                            pass
                                        _slot_mins3 = None
                                        try:
                                            from app.models.booking import BusinessSchedule as _BS3c
                                            from sqlalchemy.future import select as _sel3c
                                            _s3c_res = await db.execute(_sel3c(_BS3c).where(_BS3c.org_id == org.id))
                                            _s3c = _s3c_res.scalars().first()
                                            if _s3c:
                                                _slot_mins3 = getattr(_s3c, "slot_duration_minutes", None)
                                        except Exception:
                                            pass
                                        directive_meta["description"] = _format_time_prompt_for_whatsapp(
                                            str(_target_date.isoformat()) if '_target_date' in locals() else str(_target or ""),
                                            str(_tz_res.get("effective_timezone") or _tz_name),
                                            _slot_mins3,
                                            weekly_schedule=_weekly3,
                                            exceptions=_excs3,
                                        )
                                    except Exception:
                                        pass
                    except Exception as _e:
                        logger.warning(f"Directive option generation failed for {_dtype}: {_e}", exc_info=True)
                        _raw = []
                for _i, _o in enumerate((_raw or [])[:10]):
                    _label = str(_o) if isinstance(_o, str) else str(_o.get("label") or _o.get("title") or _o)
                    # For SELECT_DATE, value must be ISO
                    _value = _label
                    _oid = f"{_dtype.lower()}_{_i}_{_label.replace(' ', '_')[:20]}"
                    if _dtype == _DT.SELECT_DATE:
                        try:
                            from dateutil.parser import parse as _parse_date

                            _parsed = _parse_date(_label)
                            if _parsed.year < 2000:
                                from datetime import datetime as _dt

                                _now_year = _dt.now().year
                                try:
                                    _parsed = _parse_date(f"{_label} {_now_year}")
                                except Exception:
                                    pass
                            _value = _parsed.date().isoformat()
                            _oid = f"date_{_value.replace('-', '')}"
                        except Exception:
                            _value = _label
                            _oid = f"date_{_label.replace(' ', '_').replace(',', '')[:20]}"
                    elif _dtype == _DT.SELECT_SLOT:
                        # Stable id with date prefix
                        _target_for_id = None
                        if isinstance(directive_meta, dict):
                            _target_for_id = directive_meta.get("target_date") or directive_meta.get("date")
                        if not _target_for_id:
                            _target_for_id = (chat_session.collected_data or {}).get("date") or "slot"
                        _oid = f"slot_{str(_target_for_id).replace('-', '')}_{_label.replace(':', '').replace(' ', '')}"
                        _value = _label
                    _opts.append({"id": _oid, "label": _label, "value": _value})
                if _dtype == _DT.CONFIRM_BOOKING and not _opts:
                    _opts = [{"id": "confirm_booking", "label": "CONFIRM", "value": "confirm"}, {"id": "change_booking", "label": "CHANGE", "value": "change"}]
                if _opts:
                    # No BookingDirective row is written on WhatsApp. The computed
                    # options flow straight into field_meta for the text-only
                    # render, and the ActionPayload widget_directive drives flow
                    # control. The date step offers slot-checked days rather than
                    # the raw calendar list, so a closed or fully-booked day is
                    # never put in front of the customer.
                    if _opts and isinstance(directive_meta, dict):
                        if _dtype == _DT.SELECT_DATE:
                            try:
                                from app.engine.scheduling_policy import (
                                    select_date_option_labels as _sel_dates,
                                )

                                _bookable = await _sel_dates(
                                    db=db,
                                    org_id=str(org.id),
                                    org_timezone=str(
                                        getattr(org_config, "timezone", None) or "UTC"
                                    ),
                                )
                                if _bookable:
                                    _opts = _bookable
                            except Exception:
                                pass
                        directive_meta["options"] = (
                            [dict(o) for o in _opts]
                            if _opts and isinstance(_opts[0], dict)
                            else _opts
                        )
                    if isinstance(field_meta, dict) and isinstance(directive_meta, dict):
                        for _k, _v in directive_meta.items():
                            field_meta[_k] = _v
        except Exception as _e:
            logger.exception(f"Directive create failed: {_e}")
    # WhatsApp date step presentation.
    #
    # The customer is told the booking window in words, not shown a list of day
    # names:
    #
    #   Select your preferred date
    #
    #   We are available from 29 September 2026 to 29 September 2027, please
    #   choose a date within this horizon. (All dates in Africa/Algiers.)
    #
    # A "Available dates: Thu, Oct 01 ..." line was tried and reverted. It
    # reads as a menu, and any day on it can turn out to be full, so the
    # customer picks from it and is then told the day is unavailable. The
    # horizon plus the closed-day note is what they need, and it cannot go
    # stale between being sent and being used.
    if _is_whatsapp and directive == "REQUIRE_DATE_PICKER":
        try:
            from app.engine.message_processor import (
                _format_available_dates_for_prompt as _fmt_wa_date,
            )
            from app.engine.scheduling_policy import (
                resolve_booking_horizon_days as _wa_horizon,
            )

            _wa_tz = str(getattr(org_config, "timezone", None) or "UTC")
            try:
                _wa_max = (await _wa_horizon(db, org.id, org_config))[0]
            except Exception:
                _wa_max = 90
            _wa_desc = _fmt_wa_date(_wa_tz, _wa_max)
            if _wa_desc and isinstance(assistant_message, str) and assistant_message.strip():
                _wa_head = "Select your preferred date"
                if _wa_desc not in assistant_message:
                    assistant_message = (
                        f"{assistant_message.rstrip()}\n\n{_wa_head}\n\n{_wa_desc}"
                        if _wa_head.lower() not in assistant_message.lower()
                        else f"{assistant_message.rstrip()}\n\n{_wa_desc}"
                    )
                logger.info("DATE_STEP_FORMAT_APPLIED tz=%s", _wa_tz)
        except Exception as _wa_date_err:
            logger.warning("date step format failed: %s", _wa_date_err)
    is_valid_directive, validation_error = StateEvaluator.validate_directive_payload(
        directive,
        directive_meta,
    )

    if not is_valid_directive:
        logger.warning(
            "directive_metadata_invalid",
            extra={
                "session_id": session_id,
                "directive": directive,
                "reason": validation_error,
            },
        )
        directive = "TEXT_INPUT"
        field_meta["directive_fallback"] = "TEXT_INPUT"
        field_meta["directive_validation_error"] = validation_error
    elif isinstance(directive_meta, dict):
        field_meta.update(directive_meta)

    validation_errors = result.get("validation_errors")
    if isinstance(validation_errors, dict):
        if isinstance(follow_up_fields, list):
            for core_field in ("phone", "email"):
                if (
                    isinstance(validation_errors.get(core_field), str)
                    and validation_errors.get(core_field, "").strip()
                ):
                    if core_field in follow_up_fields:
                        follow_up_fields.remove(core_field)
                    follow_up_fields.insert(0, core_field)
            chat_session.missing_fields = follow_up_fields
        active_field = str(field_meta.get("name") or "").strip()
        active_error = validation_errors.get(active_field)
        if isinstance(active_error, str) and active_error.strip():
            field_meta["validation_error"] = active_error.strip()
            if directive in {"REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"}:
                field_meta["directive_retry"] = directive
            else:
                field_meta["directive_retry"] = True

    date_reset_required = bool(result.get("date_reset_required"))
    if date_reset_required:
        for stale_key in ("selected_slot_id", "selected_slot_start_utc", "time", "date"):
            if isinstance(chat_session.collected_data, dict):
                chat_session.collected_data.pop(stale_key, None)
        follow_up_fields = ["date"]
        chat_session.missing_fields = ["date"]
        session_state_dirty = True
        directive = "REQUIRE_DATE_PICKER"
        field_meta.update(
            {
                "name": "date",
                "type": "date",
                "label": "Preferred Date",
                "directive_retry": "REQUIRE_DATE_PICKER",
                "follow_up_fields": ["date"],
                "fallback_reason": str(result.get("fallback_reason") or "invalid_date_input"),
                "fallback_next_step": str(result.get("fallback_next_step") or "pick_another_date"),
                "date_reset_required": True,
            }
        )

    if result.get("slot_unavailable"):
        refreshed_slots = result.get("refreshed_slots")
        options = refreshed_slots if isinstance(refreshed_slots, list) else []
        if options and not date_reset_required:
            directive = "REQUIRE_TIME_SLOT_GRID"
            field_meta.update(
                {
                    "name": "time",
                    "type": "time",
                    "label": "Preferred Time",
                    "validation_error": "That slot is no longer available. Please choose another time.",
                    "directive_retry": "REQUIRE_TIME_SLOT_GRID",
                    "slot_unavailable": True,
                    "options": options,
                }
            )
        elif not date_reset_required:
            directive = "REQUIRE_DATE_PICKER"
            follow_up_fields = ["date"]
            chat_session.missing_fields = ["date"]
            session_state_dirty = True
            field_meta.update(
                {
                    "name": "date",
                    "type": "date",
                    "label": "Preferred Date",
                    "validation_error": "No slots are currently available for that date. Please pick another date.",
                    "directive_retry": "REQUIRE_DATE_PICKER",
                    "slot_unavailable": True,
                    "follow_up_fields": ["date"],
                    "fallback_reason": str(result.get("fallback_reason") or "no_slots_available_for_date"),
                    "fallback_next_step": str(result.get("fallback_next_step") or "pick_another_date"),
                    "date_reset_required": True,
                }
            )

    # Knowledge-answer marker: the graph injected company materials this
    # turn, so the visible reply may carry retrieved facts. WhatsApp renders
    # a pending booking step INSTEAD of the reply — the marker tells the
    # transports to show both (answer + step) rather than dropping the answer.
    if result.get("knowledge_answer") and isinstance(field_meta, dict):
        field_meta["knowledge_answer"] = True

    booking_field_meta: Optional[Dict[str, Any]] = None
    raw_require_otp = getattr(org_config, "require_otp", True)
    require_otp = True if raw_require_otp is None else bool(raw_require_otp)
    raw_require_phone_otp = getattr(org_config, "require_phone_otp", False)
    require_phone_otp = bool(raw_require_phone_otp)
    # OTP (email) — only when user explicitly CONFIRMS booking (PROCESS_BOOKING directive)
    # NOTE: the LangGraph node never returns a "directive" key, so fall back
    # to the deterministic StateEvaluator `directive` computed above. The old
    # `str(result.get("directive") or "")` wiped it to "" every turn, which
    # broke the confirm -> OTP -> booking chain (confirm looped forever).
    directive = str(result.get("directive") or directive or "")
    otp_required = (
        require_otp
        and directive == "PROCESS_BOOKING"
        and not chat_session.otp_verified
    )
    # Phone OTP — WhatsApp BYO-OTP; independent of email gate
    # Only require phone OTP when user explicitly CONFIRMS the booking (PROCESS_BOOKING directive)
    # Not when all fields are merely collected (REQUEST_CONFIRMATION)
    phone_otp_required = (
        require_phone_otp
        and not getattr(chat_session, "phone_otp_verified", False)
        and str(chat_session.collected_data.get("phone") or "").strip() != ""
        and directive == "PROCESS_BOOKING"  # user explicitly confirmed
    )
    otp_event_key = str(
        result.get("otp_event_key")
        or f"otp:{session_id}:{chat_session.collected_data.get('email', '')}"
    )
    _behavior_profile: Dict[str, Any] = {}
    try:
        from app.engine.user_behavior import analyze_user_behavior as _analyze_behavior

        _behavior_profile = _analyze_behavior(
            message_text,
            missing_fields=list(chat_session.missing_fields or []),
            collected_data=dict(chat_session.collected_data or {}),
        ) or {}
    except Exception:
        _behavior_profile = {}
    _behavior_primary = str(_behavior_profile.get("primary_intent") or "")
    _behavior_knowledge = bool(_behavior_profile.get("should_answer_knowledge"))
    _behavior_oos = (
        _behavior_primary == "unclear"
        and len(str(message_text or "").split()) >= 2
        and not re.search(r"\d", str(message_text or ""))
        and not (chat_session.missing_fields or [])
        and not otp_required
        and not phone_otp_required
        and current_intent not in ("CONFIRM_BOOKING", "CANCEL_BOOKING", "EDIT_DATA", "CHECK_BOOKING")
    )
    if _behavior_oos:
        _business = str(
            (org_config_dict or {}).get("business_name") or getattr(org, "name", "") or ""
        ).strip() or "this business"
        _oos_msg = (
            f"That's outside what I can help with — I handle appointment bookings "
            f"and questions about {_business}. "
            f"Let me know if there's anything else I can do for you!"
        )
        await crud_chat.update_chat_session(db, session_id, chat_session)
        _oos_action = ActionPayload(
            message=_oos_msg, widget_directive="TEXT_INPUT", field_meta=field_meta
        )
        await _persist_transcript_best_effort(
            db, session_id=session_id, user_message=_transcript_user_text, user_external_id=_inbound_id,
            assistant_message=_oos_msg, directive="TEXT_INPUT", intent=chat_session.current_intent,
        )
        return _oos_action
    # ── Single active booking per customer ──
    # Check BEFORE OTP so we never send a code for a booking that cannot proceed.
    # Mirrors message_processor._handle_booking_flow duplicate guard.
    # Skip for knowledge/decline turns — questions must be answered, not blocked.
    _is_non_booking_turn = str(current_intent).lower() in (
        "ask_question",
        "decline_booking",
        "declined_booking",
    ) or bool(result.get("knowledge_answer") or field_meta.get("knowledge_answer")) or _behavior_knowledge
    if not _is_non_booking_turn:
        try:
            from app.engine.cancel_flow import _find_upcoming_booking as _find_dup_booking
            from app.engine.cancel_flow import _format_slot_label as _dup_slot_label

            _dup_booking = None
            _dup_start = None
            _dup_email_for_check = resolve_email_from_collected_data(
                chat_session.collected_data, list(fd_map.values()) if isinstance(fd_map, dict) else None
            )
            _dup_phone_for_check = str((chat_session.collected_data or {}).get("phone") or "").strip()
            if _dup_email_for_check:
                _dup_booking, _dup_start = await _find_dup_booking(db, org, "email", _dup_email_for_check)
            if _dup_booking is None and _dup_phone_for_check:
                _dup_booking, _dup_start = await _find_dup_booking(db, org, "phone", _dup_phone_for_check)
            if _dup_booking is not None:
                _dup_slot = _dup_slot_label(_dup_start, org_config) if _dup_start else ""
                _dup_date = ""
                if _dup_start:
                    try:
                        from app.engine.cancel_flow import _org_timezone as _dup_tz

                        _dup_date = _dup_start.astimezone(_dup_tz(org_config)).strftime("%d %b %Y")
                    except Exception:
                        _dup_date = ""
                _dup_when = f"{_dup_date} at {_dup_slot}".strip() if _dup_date or _dup_slot else "upcoming"
                _dup_msg = (
                    f"You already have an appointment booked ({_dup_when}).\n\n"
                    f"Name: {_dup_booking.customer_name}\n"
                    f"Email: {_dup_booking.customer_email}\n"
                    f"{'Phone: ' + str(_dup_booking.customer_phone) + chr(10) if _dup_booking.customer_phone else ''}"
                    f"We allow only one active booking at a time. "
                    f"To book a new slot, please cancel the existing one first — "
                    f"just say 'cancel my booking'."
                )
                field_meta["duplicate_booking"] = True
                field_meta["booking_id"] = str(_dup_booking.id)
                assistant_message = _dup_msg
                directive = "TEXT_INPUT"
                # Do not send OTP — return duplicate info directly
                # Persist and return (mirrors _handle_booking_flow early return)
                action = ActionPayload(message=assistant_message, widget_directive=directive, field_meta=field_meta)
                await _persist_transcript_best_effort(
                    db, session_id=session_id, user_message=_transcript_user_text, user_external_id=_inbound_id,
                    assistant_message=action.message, directive=directive, intent=chat_session.current_intent,
                )
                return action
        except Exception as _dup_err:
            logger.warning("duplicate booking guard failed open (graph): %s", _dup_err)

    from app.core.otp_delivery import delivery_error, delivery_succeeded
    from app.crud.otp import invalidate_otp_delivery
    from app.core.config import settings as _otp_delivery_settings

    booking_gate_handled = False
    # Mandatory-OTP gate.
    #
    # This must NOT depend on the directive the model happened to choose. A
    # security gate keyed off an LLM output is bypassable: when the graph
    # answered REQUEST_CONFIRMATION instead of PROCESS_BOOKING (all fields
    # collected), the OTP was never sent, the user was shown a "review your
    # booking" summary, and saying "okay" produced "your appointment is
    # confirmed" with no verification and no Booking row.
    #
    # So the gate is driven by state, not by the directive: whenever OTP is
    # required by admin config, is not yet verified, and every required field
    # is collected, the booking flow runs and its OTP branch sends the code.
    _otp_pending = bool(
        (otp_required and not getattr(chat_session, "otp_verified", False))
        or (
            phone_otp_required
            and not getattr(chat_session, "phone_otp_verified", False)
        )
    )
    _all_required_collected = not (chat_session.missing_fields or [])
    # Did the customer actually confirm on THIS turn? Taken from the inbound
    # text with the whole-word confirm matcher, never from the model's intent
    # label. This is what authorises the booking itself.
    _user_confirmed = _is_user_confirmation(message_text)
    if should_run_booking_gate(
        directive=directive,
        missing_fields=chat_session.missing_fields,
        # Config-level requirements, NOT the derived `otp_required` /
        # `phone_otp_required` locals: those are False once a code is accepted
        # and would switch the gate off on the booking turn.
        require_otp=bool(require_otp),
        require_phone_otp=bool(require_phone_otp),
        otp_verified=bool(getattr(chat_session, "otp_verified", False)),
        phone_otp_verified=bool(getattr(chat_session, "phone_otp_verified", False)),
        user_confirmed=_user_confirmed,
    ):
        (
            assistant_message,
            directive,
            booking_field_meta,
        ) = await message_processor._handle_booking_flow(
            db,
            org,
            org_config,
            chat_session,
            current_intent,
            require_otp,
            fd_map,
            background_tasks,
            dispatch_notifications=False,
            require_phone_otp=require_phone_otp,
            user_confirmed=_user_confirmed,
        )
        if isinstance(booking_field_meta, dict):
            field_meta.update(booking_field_meta)
        booking_gate_handled = True
        # The off-topic strike counter is cleared once, at the final authority
        # point, so every path (booking, knowledge, decline) is accounted for
        # exactly once instead of risking double counting.

        if may_send_otp_standalone(
            booking_gate_handled=booking_gate_handled,
            otp_required=otp_required,
            user_confirmed=_user_confirmed,
        ):
            effect_state = _active_effect_state(chat_session)
            sent_keys = effect_state.get("otp_sent_keys")
            if not isinstance(sent_keys, list):
                sent_keys = []
                effect_state["otp_sent_keys"] = sent_keys

            otp_error: Optional[str] = None
            recipient_email = resolve_email_from_collected_data(
                chat_session.collected_data,
                list(fd_map.values()),
            )
            # Stale-key guard: otp_sent_keys persists even after OTPRecord expired/deleted
            # (cleanup every 5m). If the record is gone, allow regeneration even if the
            # key is still in sent_keys. Without this, users get stuck on
            # "I've sent..." with no code actually deliverable.
            _otp_exists = False
            try:
                import uuid as _uuid_exist
                from sqlalchemy.future import select as _sel_exist

                from app.models.otp import OTPRecord as _OTPExist

                try:
                    _sid_uuid = _uuid_exist.UUID(str(session_id))
                except Exception:
                    _sid_uuid = session_id
                _otp_row = (
                    await db.execute(
                        _sel_exist(_OTPExist).where(
                            _OTPExist.session_id == _sid_uuid, _OTPExist.channel == "email"
                        )
                    )
                ).scalars().first()
                _otp_exists = _otp_row is not None
                # Also check expiry - if expired, treat as not exists (will be cleaned)
                if _otp_exists:
                    from datetime import datetime, timezone as _tz

                    _now = datetime.now(_tz.utc)
                    _exp = _otp_row.expires_at
                    if _exp.tzinfo is None:
                        _exp = _exp.replace(tzinfo=_tz.utc)
                    if _now > _exp:
                        _otp_exists = False
                        # remove stale key so next turn regenerates
                        if otp_event_key in sent_keys:
                            sent_keys.remove(otp_event_key)
            except Exception:
                _otp_exists = False
                if otp_event_key in sent_keys:
                    sent_keys.remove(otp_event_key)
            if otp_event_key not in sent_keys or not _otp_exists:
                try:
                    if not recipient_email:
                        raise ValueError("We couldn't find an email address to send the code to.")
                    otp_code = await otp_crud.generate_otp(
                        db,
                        chat_session.session_id,
                        resend=False,
                    )
                    _result = await mailer.send_otp_email(
                        recipient_email,
                        otp_code,
                        org_id=org.id,
                        idempotency_key=otp_event_key,
                    )
                    if not delivery_succeeded(
                        _result,
                        allow_mock=bool(
                            getattr(_otp_delivery_settings, "OTP_ALLOW_MOCK_DELIVERY", False)
                        ),
                    ):
                        await invalidate_otp_delivery(
                            db,
                            chat_session.session_id,
                            "email",
                            context_key=otp_event_key,
                        )
                        raise RuntimeError(delivery_error(_result))
                    sent_keys.append(otp_event_key)
                    await crud_chat.update_chat_session(db, session_id, chat_session)
                except Exception as otp_send_error:
                    otp_error = str(otp_send_error)

            try:
                otp_policy_meta = await otp_crud.get_otp_policy_metadata(
                    db, chat_session.session_id
                )
            except Exception:
                otp_policy_meta = {}

            directive = "REQUIRE_OTP_MODAL"
            field_meta.update(otp_policy_meta)
            field_meta["otp_required"] = True
            field_meta["otp_event_key"] = otp_event_key
            field_meta["channel"] = "email"
            if otp_error:
                field_meta["validation_error"] = otp_error
                assistant_message = (
                    "I couldn't send the verification code right now. "
                    "Please check your contact details and try again."
                )
            elif recipient_email:
                assistant_message = (
                    f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. "
                    "Please enter it here to continue."
                )
            else:
                assistant_message = "Please verify your OTP code to continue."
    # Knowledge question while OTP pending — answer it, don't spam OTP.
    # The graph injected knowledge (knowledge_answer) for ASK_QUESTION;
    # forcing OTP here would ignore the user's actual question and loop.
    _is_knowledge_turn = bool(result.get("knowledge_answer") or field_meta.get("knowledge_answer")) or str(
        current_intent
    ) == "ASK_QUESTION" or _behavior_knowledge
    if not booking_gate_handled and _is_knowledge_turn:
        # Deliberately NOT gated on a pending OTP. This branch is the single
        # place an off-topic answer is produced, so gating it on
        # phone_otp_required meant the off-topic guardrail only ran mid-booking:
        # a customer with no booking in progress could ask the assistant
        # anything indefinitely and it never counted towards a cooldown.
        # A pending code is still not re-sent here; it is only left untouched.
        # Return the LLM's knowledge answer with TEXT_INPUT and keep any
        # pending OTP as it is.
        _knowledge_reply = str(assistant_message or "").strip()
        # Don't re-send OTP on knowledge turn; just remind
        try:
            _policy_for_reminder = await otp_crud.get_otp_policy_metadata(
                db, chat_session.session_id, channel="phone"
            )
        except Exception:
            _policy_for_reminder = {}
        field_meta.update(_policy_for_reminder)
        field_meta["otp_required"] = True
        field_meta["channel"] = "phone"
        field_meta["knowledge_answer"] = True
        # Normalise the decline so the customer gets one consistent, warm
        # answer. The model reaches for several different cold phrasings
        # ("...in our knowledge materials", "the company knowledge
        # materials don't include...", "I don't have information about
        # that") which read as a brush-off and mention internal machinery the
        # customer has never heard of.
        _knowledge_reply = _normalise_offtopic_decline(_knowledge_reply)
        # A question between booking steps is answered on its own terms. The
        # "back to your booking" push is removed so the answer stands alone;
        # the pending step is re-asked on the next turn.
        _knowledge_reply = _strip_booking_redirect(_knowledge_reply)

        # Strike accounting is deliberately NOT done here. This branch returns
        # early, and a live test showed the declines that customers actually
        # receive are produced by other paths, so counting here recorded zero
        # strikes and no cooldown ever opened. Counting now happens once, at
        # the final authority point after every path has run.

        # Return early, bypassing OTP generation for this knowledge turn.
        #
        # The OTP reminder is deliberately NOT appended. A customer asking an
        # unrelated question mid-booking got a clean decline with a
        # "your verification code is still waiting" line stapled underneath,
        # which reads as a payment demand bolted onto a refusal and makes the
        # bot look like it is nagging rather than helping. The code is not
        # lost: once a code is actually pending, the next turn prompts for it,
        # and 'resend' always works. Keep the reply to the question asked.
        #
        # A confirmation claim is still blocked here. This path returns before
        # the end-of-turn guard, and it is exactly where production sent "Your
        # appointment is booked for October 2, 2026 at 3:00 PM" with no Booking
        # row on disk.
        _k_final_msg = _knowledge_reply or (
            "I'm a booking assistant, so I can only help with appointments — "
            "I don't have information about that."
        )
        _k_guarded = await _guard_confirmation_claim(
            db, session_id, _k_final_msg, "TEXT_INPUT",
            chat_session, field_meta, org, fd_map,
        )
        if _k_guarded is not None:
            _k_final_msg, _k_dir, field_meta = _k_guarded
        else:
            _k_dir = "TEXT_INPUT"
        _action = ActionPayload(
            message=_k_final_msg,
            widget_directive=_k_dir,
            field_meta=field_meta,
        )
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=_transcript_user_text, user_external_id=_inbound_id,
            assistant_message=_action.message,
            directive=_k_dir,
            intent=current_intent,
        )
        return _action
    elif not booking_gate_handled and _is_knowledge_turn and not (chat_session.missing_fields or []):
        _k_answer = str(assistant_message or "").strip()
        if _k_answer and all(
            frag not in _k_answer.lower()
            for frag in ("internal error", "trouble connecting", "hard to process")
        ):
            field_meta["knowledge_answer"] = True
            # Same early-return hole as the knowledge path above.
            _k2_guarded = await _guard_confirmation_claim(
                db, session_id, _k_answer, "TEXT_INPUT",
                chat_session, field_meta, org, fd_map,
            )
            if _k2_guarded is not None:
                _k_answer, _k2_dir, field_meta = _k2_guarded
            else:
                _k2_dir = "TEXT_INPUT"
            _k_action = ActionPayload(
                message=_k_answer, widget_directive=_k2_dir, field_meta=field_meta
            )
            await _persist_transcript_best_effort(
                db,
                session_id=session_id,
                user_message=_transcript_user_text, user_external_id=_inbound_id,
                assistant_message=_k_answer,
                directive=_k2_dir,
                intent=current_intent,
            )
            return _k_action
        directive = "TEXT_INPUT"
    elif may_send_otp_standalone(
        booking_gate_handled=booking_gate_handled,
        otp_required=phone_otp_required,
        user_confirmed=_user_confirmed,
    ):
        # Phone verification to confirm booking — provider abstraction
        # (WhatsApp active, SMS fallback), same as legacy path + /generate.
        phone = str(chat_session.collected_data.get("phone") or "").strip()
        # Normalize phone for dedupe key so "63500 63338" == "6350063338"
        try:
            import re as _re_key
            import phonenumbers as _pn_key

            _pn = _pn_key.parse(phone, "IN" if len(phone.replace(" ", "").replace("-", "").lstrip("+")) <= 11 else None)
            _phone_norm = _pn_key.format_number(_pn, _pn_key.PhoneNumberFormat.E164) if _pn_key.is_possible_number(_pn) else _re_key.sub(r"\D", "", phone)
        except Exception:
            import re as _re_key

            _phone_norm = _re_key.sub(r"\D", "", phone)
        phone_event_key = f"otp_phone:{session_id}:{_phone_norm}:{chat_session.collected_data.get('date','')}:{chat_session.collected_data.get('time','')}"
        effect_state = _active_effect_state(chat_session)
        sent_keys = effect_state.get("otp_sent_keys")
        if not isinstance(sent_keys, list):
            sent_keys = []
            effect_state["otp_sent_keys"] = sent_keys
        otp_error = None
        # Stale-key guard for phone: if OTPRecord expired/deleted but key persists in
        # sent_keys, we would otherwise skip generation and user gets "sent" with no code.
        _phone_otp_exists = False
        try:
            import uuid as _uuid_exist2
            from sqlalchemy.future import select as _sel_exist2

            from app.models.otp import OTPRecord as _OTPExist2

            try:
                _sid_uuid2 = _uuid_exist2.UUID(str(session_id))
            except Exception:
                _sid_uuid2 = session_id
            _otp_row2 = (
                await db.execute(
                    _sel_exist2(_OTPExist2).where(
                        _OTPExist2.session_id == _sid_uuid2, _OTPExist2.channel == "phone"
                    )
                )
            ).scalars().first()
            _phone_otp_exists = _otp_row2 is not None
            if _phone_otp_exists:
                from datetime import datetime, timezone as _tz2

                _now2 = datetime.now(_tz2.utc)
                _exp2 = _otp_row2.expires_at
                if _exp2.tzinfo is None:
                    _exp2 = _exp2.replace(tzinfo=_tz2.utc)
                if _now2 > _exp2:
                    _phone_otp_exists = False
                    if phone_event_key in sent_keys:
                        sent_keys.remove(phone_event_key)
        except Exception:
            _phone_otp_exists = False
            if phone_event_key in sent_keys:
                sent_keys.remove(phone_event_key)
        if phone_event_key not in sent_keys or not _phone_otp_exists:
            try:
                otp_code = await otp_crud.generate_otp(
                    db, chat_session.session_id, channel="phone", resend=False
                )
                from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider
                _otp_provider = WhatsAppProvider()
                _result = await _otp_provider.send_otp(
                    phone, otp_code, str(chat_session.session_id)
                )
                if not delivery_succeeded(
                    _result,
                    allow_mock=bool(
                        getattr(_otp_delivery_settings, "OTP_ALLOW_MOCK_DELIVERY", False)
                    ),
                ):
                    await invalidate_otp_delivery(
                        db,
                        chat_session.session_id,
                        "phone",
                        context_key=phone_event_key,
                    )
                    raise RuntimeError(delivery_error(_result))
                sent_keys.append(phone_event_key)
                await crud_chat.update_chat_session(db, session_id, chat_session)
            except Exception as otp_send_error:
                otp_error = str(otp_send_error)
        try:
            otp_policy_meta = await otp_crud.get_otp_policy_metadata(
                db, chat_session.session_id, channel="phone"
            )
        except Exception:
            otp_policy_meta = {}
        directive = "REQUIRE_OTP_MODAL"
        field_meta.update(otp_policy_meta)
        field_meta["otp_required"] = True
        field_meta["channel"] = "phone"
        field_meta["otp_event_key"] = phone_event_key
        if otp_error:
            field_meta["validation_error"] = otp_error
        masked = phone[:4] + "***" + phone[-2:] if len(phone) > 6 else phone[:3] + "***"
        via = "WhatsApp"
        if otp_error:
            assistant_message = (
                "I couldn't send the verification code right now. "
                "Please check your phone number and try again."
            )
        else:
            assistant_message = (
                f"Perfect! I've sent a verification code to {masked} via {via}. "
                "Please enter the 6-digit code here to confirm your booking."
            )
    elif not booking_gate_handled and directive == "REQUEST_CONFIRMATION":
        captured_deltas = field_meta.get("captured_deltas") if isinstance(field_meta, dict) else {}
        has_captured_changes = isinstance(captured_deltas, dict) and bool(captured_deltas)
        if current_intent == "EDIT_DATA" and not has_captured_changes:
            directive = "TEXT_INPUT"
            field_meta.update(
                {
                    "name": "edit_details",
                    "type": "text",
                    "label": "Update Details",
                    "placeholder": "Tell me what you want to change (name, email, phone, date, or time).",
                }
            )
            assistant_message = "Sure - tell me what you'd like to change (name, email, phone, date, or time)."
        else:
            assistant_message, directive = message_processor._build_confirmation_summary(
                chat_session, fd_map
            )
    elif not booking_gate_handled and directive == "PROCESS_BOOKING":
        (
            assistant_message,
            directive,
            booking_field_meta,
        ) = await message_processor._handle_booking_flow(
            db,
            org,
            org_config,
            chat_session,
            current_intent,
            require_otp,
            fd_map,
            background_tasks,
            dispatch_notifications=False,
            require_phone_otp=require_phone_otp,
            user_confirmed=_user_confirmed,
        )
        if isinstance(booking_field_meta, dict):
            field_meta.update(booking_field_meta)

    if directive == "BOOKING_CONFIRMED_CARD" and chat_session.session_status != "CONFIRMED":
        logger.warning(
            "booking_confirmation_blocked_missing_persistence",
            extra={
                "session_id": session_id,
                "session_status": chat_session.session_status,
            },
        )
        assistant_message, directive = message_processor._build_confirmation_summary(
            chat_session, fd_map
        )

    booking_confirmed = (
        directive == "BOOKING_CONFIRMED_CARD"
        and str(chat_session.session_status).upper() == "CONFIRMED"
    )
    if booking_confirmed:
        confirmation_event_key = _build_confirmation_event_key(
            session_id,
            dict(chat_session.collected_data or {}),
        )
        field_meta["booking_confirmed"] = True
        field_meta["confirmation_event_key"] = confirmation_event_key
        field_meta["confirmation_provenance"] = "orchestrator_commit_outcome"

        effect_state = _active_effect_state(chat_session)
        sent_confirmations = effect_state.get("confirmation_sent_keys")
        if not isinstance(sent_confirmations, list):
            sent_confirmations = []
            effect_state["confirmation_sent_keys"] = sent_confirmations

        if confirmation_event_key not in sent_confirmations:
            booking_details = dict(chat_session.collected_data or {})
            booking_row = None
            try:
                from app.models.booking import Booking
                from sqlalchemy import select as _sel
                _res = await db.execute(_sel(Booking).where(Booking.session_id == session_id, Booking.org_id == org.id).order_by(Booking.created_at.desc()).limit(1))
                booking_row = _res.scalars().first()
                if booking_row:
                    booking_details["booking_id"] = str(booking_row.id)
                    if not booking_details.get("email") and booking_row.customer_email:
                        booking_details["email"] = booking_row.customer_email
                    if not booking_details.get("name") and booking_row.customer_name:
                        booking_details["name"] = booking_row.customer_name
                    if getattr(booking_row, "meeting_type_snapshot", None):
                        booking_details["meeting_type_snapshot"] = booking_row.meeting_type_snapshot
                    if getattr(booking_row, "meeting_link_status", None):
                        _st = booking_row.meeting_link_status
                        booking_details["meeting_link_status"] = _st.value if hasattr(_st, "value") else str(_st)
                    if getattr(booking_row, "meeting_link_url", None):
                        booking_details["meeting_link_url"] = booking_row.meeting_link_url
                    if getattr(booking_row, "meeting_link_provider", None):
                        _pv = booking_row.meeting_link_provider
                        booking_details["meeting_link_provider"] = _pv.value if hasattr(_pv, "value") else str(_pv)
                    if getattr(booking_row, "user_timezone", None) and "user_timezone" not in booking_details:
                        booking_details["user_timezone"] = booking_row.user_timezone
                    if getattr(booking_row, "org_timezone_snapshot", None) and "org_timezone_snapshot" not in booking_details:
                        booking_details["org_timezone_snapshot"] = booking_row.org_timezone_snapshot
            except Exception as _enrich_err:
                logger.warning(f"Failed to enrich booking_details with meeting link for email: {_enrich_err}")
            booking_details["source_channel"] = (
                "WhatsApp"
                if str(getattr(chat_session, "channel", "") or "").lower()
                in {"whatsapp", "whatsapp_cloud"}
                else "Web Widget"
            )
            customer_email = str(
                booking_details.get("email")
                or getattr(booking_row, "customer_email", "")
                or ""
            ).strip()
            admin_email = str(booking_details.get("admin_email") or "").strip()
            if not admin_email:
                try:
                    from app.models.user import User as _EmailAdminUser
                    _admin_result = await db.execute(
                        select(_EmailAdminUser).where(_EmailAdminUser.org_id == org.id)
                    )
                    _admin_user = _admin_result.scalars().first()
                    admin_email = str(getattr(_admin_user, "email", "") or "").strip()
                except Exception:
                    admin_email = ""
            try:
                _fresh = resolve_email_from_collected_data(chat_session.collected_data, list(fd_map.values()))
                if _fresh and _fresh != customer_email:
                    customer_email = _fresh
                    booking_details["email"] = _fresh
            except Exception:
                pass
            if customer_email:
                _masked_email = (customer_email[:1] + "***@" + customer_email.split("@")[-1]) if "@" in customer_email else "***"
                logger.info(
                    "Booking confirmation email queued for %s via %s (session %s)",
                    _masked_email,
                    "phone-otp" if getattr(chat_session, "phone_otp_verified", False) else "email-otp/none",
                    session_id,
                )
                try:
                    if background_tasks:
                        background_tasks.add_task(
                            mailer.send_confirmation_emails,
                            customer_email,
                            admin_email,
                            booking_details,
                            org_id=org.id,
                        )
                    else:
                        await mailer.send_confirmation_emails(
                            customer_email,
                            admin_email,
                            booking_details,
                            org_id=org.id,
                        )
                except Exception as confirmation_error:
                    logger.error(
                        "confirmation_dispatch_failed",
                        extra={
                            "session_id": session_id,
                            "error": str(confirmation_error),
                        },
                    )
                    field_meta["email_retrying"] = True
                sent_confirmations.append(confirmation_event_key)
                await crud_chat.update_chat_session(db, session_id, chat_session)

    if directive == "REQUIRE_OTP_MODAL":
        field_meta.setdefault("otp_cooldown_seconds", OTP_COOLDOWN_SECONDS)
        field_meta.setdefault("resend_cap", OTP_RESEND_CAP)
        field_meta.setdefault("valid_for_seconds", OTP_VALID_FOR_SECONDS)
        field_meta.setdefault("resends_left", OTP_RESEND_CAP)

    if isinstance(result.get("notification_status"), dict):
        notification_status = result.get("notification_status") or {}
        if notification_status.get("email_retrying") is True:
            field_meta["email_retrying"] = True
    if result.get("email_retrying") is True:
        field_meta["email_retrying"] = True

    # Populate available time slots when the widget needs the time-slot grid.
    # The graph sets the REQUIRE_TIME_SLOT_GRID directive but does not compute
    # slot options itself, so we fetch them here (unless already provided by a
    # slot_unavailable retry above).
    if directive == "REQUIRE_TIME_SLOT_GRID" and not field_meta.get("options"):
        target_date = chat_session.collected_data.get("date")
        if target_date:
            try:
                from dateutil.parser import parse as date_parse
                from app.engine.slot_engine import (
                    compute_slot_inventory,
                    format_slot_time,
                    apply_scheduling_policy_to_inventory,
                )
                from app.engine.scheduling_policy import (
                    resolve_effective_timezone_policy,
                )

                schedule_result = await db.execute(
                    select(BusinessSchedule).where(
                        BusinessSchedule.org_id == org.id
                    )
                )
                schedule = schedule_result.scalars().first()
                org_timezone = str(
                    (schedule.timezone if schedule and schedule.timezone else None)
                    or getattr(org_config, "timezone", None)
                    or "UTC"
                )

                parsed_date = date_parse(target_date).date()
                inventory, availability_status = await compute_slot_inventory(
                    db, org.id, parsed_date, org_config=org_config
                )
                timezone_resolution = resolve_effective_timezone_policy(
                    requested_tz=chat_session.collected_data.get("user_timezone"),
                    org_tz=org_timezone,
                    requested_source=chat_session.collected_data.get(
                        "timezone_source"
                    ),
                )
                inventory, availability_status, policy_reason_code = (
                    apply_scheduling_policy_to_inventory(
                        inventory,
                        parsed_date,
                        base_status=availability_status,
                        effective_timezone=str(
                            timezone_resolution["effective_timezone"]
                        ),
                        **_slot_policy_kwargs(org_config),
                    )
                )
                # Convert UTC slot times to the effective timezone for display
                try:
                    from zoneinfo import ZoneInfo
                except ImportError:
                    from backports.zoneinfo import ZoneInfo  # type: ignore
                try:
                    _tz_for_slots = ZoneInfo(str(timezone_resolution.get("effective_timezone") or org_timezone or "UTC"))
                except Exception:
                    import datetime as _dt

                    _tz_for_slots = _dt.timezone.utc
                options = []
                for item in inventory:
                    if not item.get("available"):
                        continue
                    _start = item.get("start_utc")
                    if _start is None:
                        continue
                    try:
                        if getattr(_start, "tzinfo", None) is None:
                            import datetime as _dt2

                            _start = _start.replace(tzinfo=_dt2.timezone.utc)
                        _local_time = _start.astimezone(_tz_for_slots).time()
                        options.append(format_slot_time(_local_time))
                    except Exception:
                        try:
                            options.append(format_slot_time(item["start_utc"].time()))
                        except Exception:
                            continue
                field_meta["options"] = options
                field_meta["availability_status"] = availability_status
                if policy_reason_code:
                    field_meta["policy_reason_code"] = policy_reason_code
                logger.info(
                    "computed_time_slots",
                    extra={
                        "session_id": session_id,
                        "target_date": str(target_date),
                        "slot_count": len(options),
                        "availability_status": availability_status,
                    },
                )

                # No slots available: fall back to the date picker instead of
                # asking for a time that does not exist — unless there is no
                # schedule at all (e.g. test org without BusinessSchedule),
                # in which case keep the time grid so outage tests stay actionable.
                if not options:
                    if schedule is None:
                        _legacy_slot = await _has_available_persisted_slot(
                            db,
                            org.id,
                            target_date,
                            chat_session.collected_data.get("time"),
                            org_timezone,
                            chat_session.collected_data.get("locked_slot_id"),
                        )
                        if _legacy_slot:
                            directive = "PROCESS_BOOKING"
                            field_meta["availability_status"] = "persisted_slot"
                        else:
                            logger.info("no_schedule_keep_time_grid", extra={"session_id": session_id})
                    else:
                        directive = "REQUIRE_DATE_PICKER"
                        chat_session.collected_data.pop("date", None)
                        chat_session.collected_data.pop("time", None)
                        chat_session.collected_data.pop("selected_slot_id", None)
                        chat_session.missing_fields = ["date"] + [
                            f
                            for f in (chat_session.missing_fields or [])
                            if f != "date"
                        ]
                        await crud_chat.update_chat_session(
                            db, session_id, chat_session
                        )
                        field_meta.update(
                            {
                                "name": "date",
                                "type": "date",
                                "label": "Preferred Date",
                                "directive_retry": "REQUIRE_DATE_PICKER",
                                "follow_up_fields": ["date"],
                            }
                        )
                        # Closed-days hint is now provided to the LLM via context, not hardcoded here.
                        # The agent is trained in prompts.py 9f to explain closed days naturally.
                        # Keep directive as DATE_PICKER and let LLM's response_text handle the message.
                        # Only set a minimal fallback if LLM gave empty.
                        if availability_status == "same_day_disabled":
                            field_meta["closed_reason"] = "same_day_disabled"
                            assistant_message = (
                                "Same-day booking isn't available. Please choose a future date."
                            )
                        elif availability_status == "closed_day":
                            field_meta["closed_reason"] = "closed_day"
                            assistant_message = (
                                f"We are closed on {parsed_date.strftime('%A, %B %d')}. "
                                "Please choose another date."
                            )
                        elif availability_status == "exception_closed":
                            field_meta["closed_reason"] = "exception_closed"
                            closure_reason = ""
                            try:
                                from app.models.booking import ScheduleException as _SEClosed
                                _se_res = await db.execute(
                                    select(_SEClosed).where(
                                        _SEClosed.org_id == org.id,
                                        _SEClosed.exception_date == parsed_date,
                                    )
                                )
                                _se_row = _se_res.scalars().first()
                                closure_reason = str(getattr(_se_row, "reason", "") or "").strip()
                            except Exception:
                                closure_reason = ""
                            if closure_reason:
                                field_meta["closure_reason_text"] = closure_reason
                                assistant_message = (
                                    f"We are closed on {parsed_date.strftime('%A, %B %d')}: "
                                    f"{closure_reason}. Please choose another date."
                                )
                            else:
                                assistant_message = (
                                    f"We are closed on {parsed_date.strftime('%A, %B %d')}. "
                                    "Please choose another date."
                                )
                        else:
                            field_meta["closed_reason"] = "no_available_slots"
                            assistant_message = (
                                "There are no available times on that date. Please choose another date."
                            )
            except Exception as slot_err:
                logger.warning(
                    "compute_time_slots_failed",
                    extra={"session_id": session_id, "error": str(slot_err)},
                )
                # For testability: if the original intent was time-grid (e.g. manual-time
                # test with SimpleNamespace DB), keep the grid instead of falling back
                # to date-picker. The fallback to date-picker is for real slot
                # inventory failures (no slots), not for DB mock failures in tests.
                # Keep current directive/title so the caller still asks for time.
                if directive == "REQUIRE_TIME_SLOT_GRID":
                    field_meta.setdefault("options", [])
                    # preserve time field_meta if present
                    if not field_meta.get("name"):
                        field_meta.update({"name": "time", "type": "time", "label": "Preferred Time"})
                else:
                    directive = "REQUIRE_DATE_PICKER"
                    field_meta["options"] = []
                    field_meta["availability_status"] = "no_schedule"
                    field_meta.update(
                        {
                            "name": "date",
                            "type": "date",
                            "label": "Preferred Date",
                            "directive_retry": "REQUIRE_DATE_PICKER",
                            "follow_up_fields": ["date"],
                        }
                    )
                    assistant_message = (
                        "I couldn't load the available times right now. "
                        "Please pick a date and I'll try again."
                    )

    # WhatsApp-only hours context: when asking for date/time on WhatsApp, show
    # a concise hours line contextually as per message flow (no full paragraph).
    # Web keeps its own widget copy, so never prepend this WhatsApp text there.
    if _is_whatsapp and directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"):
        _prefix = await _whatsapp_hours_prefix(db, org)
        if _prefix and isinstance(assistant_message, str) and "Hours:" not in assistant_message and "business hours" not in assistant_message.lower():
            assistant_message = f"{_prefix}\n{assistant_message}"

    # Enforce one-by-one flow: override LLM dump of "provide name, email, phone, date, time" with deterministic single-field ask
    _single_field_directives = {
        "TEXT_INPUT",
        "REQUIRE_EMAIL_INPUT",
        "REQUIRE_PHONE_INPUT",
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_SELECT",
        "REQUIRE_TEXTAREA",
        "REQUIRE_NUMBER_INPUT",
    }
    if directive in _single_field_directives:
        # Exempt the intentional edit-details prompt which lists fields by design
        if field_meta.get("name") == "edit_details":
            pass
        # A declined session gets answers only — never interrogation.
        elif decline_flow.is_declined(chat_session):
            pass
        else:
            # If LLM mentions 2+ fields or lists them, replace with deterministic single-field message
            # Use word-boundary matching so "username" doesn't count as "name"
            _lower = (assistant_message or "").lower()
            _mentions = sum(1 for _kw in ["name", "email", "phone", "date", "time"] if re.search(rf"\b{_kw}\b", _lower))
            _is_knowledge = bool((field_meta or {}).get("knowledge_answer"))
            _is_question_turn = current_intent == "ASK_QUESTION"
            _llm_user_intent = str((structured_assistant_payload or {}).get("user_intent") or "").lower()
            _is_off_topic = _llm_user_intent == "off_topic"
            if _is_knowledge or _is_question_turn or _is_off_topic:
                _need_field = str((field_meta or {}).get("name") or "")
                _visibly_asks = (
                    message_processor._response_asks_for_field(
                        assistant_message,
                        _need_field,
                        (field_meta or {}).get("label"),
                    )
                    and not looks_like_json_debris(assistant_message)
                )
                if _need_field and not _visibly_asks:
                    _deterministic_ask = message_processor._build_context_aware_response(
                        directive=directive,
                        field_meta=field_meta,
                        collected_data=dict(chat_session.collected_data or {}),
                        valid_data={},
                        errors={},
                        llm_generated_msg=assistant_message,
                    )
                    _had_validation_error = bool(
                        str((field_meta or {}).get("validation_error") or "").strip()
                    )
                    if not _deterministic_ask.strip():
                        pass
                    elif _had_validation_error or looks_like_json_debris(assistant_message):
                        assistant_message = _deterministic_ask
                    else:
                        assistant_message = f"{assistant_message}\n\n{_deterministic_ask}"
            elif _mentions > 1 or ("," in assistant_message and _mentions >= 2):
                _multi_ask = message_processor._build_context_aware_response(
                    directive=directive,
                    field_meta=field_meta,
                    collected_data=dict(chat_session.collected_data or {}),
                    valid_data={},
                    errors={},
                    llm_generated_msg=assistant_message,
                )
                if _multi_ask.strip():
                    assistant_message = _multi_ask
            else:
                # F1 next-field guard (mirrors the legacy path): the reply
                # must visibly ask for the directive's field. A single-field
                # mention of the WRONG field (e.g. asking "phone" while the
                # next missing field is "email") is replaced with the
                # deterministic ask — the LLM may never reorder required
                # fields on its own. But when the message was NOT a field
                # attempt at all (a question or refusal — no
                # validation error), the LLM's answer is KEPT and the
                # deterministic ask is appended, so answers are never
                # squashed by the flow.
                _need_field = str((field_meta or {}).get("name") or "")
                _collection_intent = str(chat_session.current_intent or "")
                # Debris check: raw JSON containing the field name would fool
                # the cue test into thinking the reply asks for the field.
                _visibly_asks = (
                    message_processor._response_asks_for_field(
                        assistant_message,
                        _need_field,
                        (field_meta or {}).get("label"),
                    )
                    and not looks_like_json_debris(assistant_message)
                )
                if (
                    _need_field
                    and _collection_intent in ("START_BOOKING", "PROVIDE_DATA")
                    and not _visibly_asks
                ):
                    _deterministic_ask = message_processor._build_context_aware_response(
                        directive=directive,
                        field_meta=field_meta,
                        collected_data=dict(chat_session.collected_data or {}),
                        valid_data={},
                        errors={},
                        llm_generated_msg=assistant_message,
                    )
                    _had_validation_error = bool(
                        str((field_meta or {}).get("validation_error") or "").strip()
                    )
                    # Machine debris is never appended — it would leak raw
                    # JSON into the chat. Replace unconditionally.
                    if not _deterministic_ask.strip():
                        pass
                    elif _had_validation_error or looks_like_json_debris(
                        assistant_message
                    ):
                        logger.info(
                            "next_field_guard(graph): reply missed '%s'; using deterministic ask",
                            _need_field,
                        )
                        assistant_message = _deterministic_ask
                    else:
                        assistant_message = f"{assistant_message}\n\n{_deterministic_ask}"

    # Only mention a code when one genuinely exists, and never on the turn that
    # just sent it.
    #
    #   no record at all            -> say nothing about codes
    #   record created this turn    -> let the "code sent" reply stand
    #   record predates this turn   -> booking-assistant line + enter the code
    #
    # `_otp_created_at_before` was captured before this turn did any work, so
    # comparing it to the live record is exact rather than inferred.
    _otp_created_at_now: Optional[Any] = None
    try:
        import uuid as _uuid_now
        from sqlalchemy.future import select as _sel_now

        from app.models.otp import OTPRecord as _OTPNow

        try:
            _sid_now = _uuid_now.UUID(str(session_id))
        except Exception:
            _sid_now = session_id
        _otp_created_at_now = (
            await db.execute(
                _sel_now(_OTPNow.created_at).where(_OTPNow.session_id == _sid_now)
            )
        ).scalars().first()
    except Exception:
        _otp_created_at_now = None

    _code_exists = _otp_created_at_now is not None
    _code_sent_this_turn = bool(
        _code_exists and _otp_created_at_before != _otp_created_at_now
    )
    _code_awaiting = bool(_code_exists and not _code_sent_this_turn)

    if _code_awaiting and isinstance(assistant_message, str):
        _inbound_is_code = bool(re.fullmatch(r"\d{6}", str(_stripped or "").strip()))
        if not _inbound_is_code:
            _channel = "phone" if (require_phone_otp and not getattr(chat_session, "phone_otp_verified", False)) else "email"
            _dest = str(chat_session.collected_data.get("phone") or resolve_email_from_collected_data(chat_session.collected_data, list(fd_map.values()) if isinstance(fd_map, dict) else None) or "your contact").strip()
            # Say WHO we are instead of silently swallowing the question. The
            # customer asked something unrelated mid-verification and used to
            # get a bare "enter the code" with no acknowledgement at all, which
            # read as being stonewalled. Acknowledge the limit, then re-prompt.
            assistant_message = (
                "I'm a booking assistant, so I can only help with "
                "appointments — I don't have information about that.\n\n"
                f"To finish your booking, please enter the verification code sent to {_dest}."
            )
            if directive == "TEXT_INPUT":
                directive = "REQUIRE_OTP_MODAL"
                field_meta["channel"] = _channel

    assistant_message = _guard_user_visible_booking_message(
        assistant_message=assistant_message,
        directive=directive,
        require_otp=require_otp,
        chat_session=chat_session,
        field_meta=field_meta,
        fd_map=fd_map,
    )

    # Truthfulness invariant: a confirmation claim needs a persisted booking.
    #
    # The directive guard above only catches BOOKING_CONFIRMED_CARD. The model
    # also writes confirmation PROSE ("your appointment is confirmed for ...")
    # inside ordinary replies, and that text reached customers verbatim while
    # no Booking row existed. So the sentence itself is checked against the
    # database before it is allowed out. This is the last line of defence:
    # nothing may tell a customer they are booked unless they are.
    if isinstance(assistant_message, str) and assistant_message.strip():
        try:
            from app.engine.user_visible_text import looks_like_confirmation_claim

            _claims_confirmation = looks_like_confirmation_claim(assistant_message)
        except Exception:
            _claims_confirmation = False
        if _claims_confirmation and not await _session_has_persisted_booking(
            db,
            session_id,
            org_id=getattr(org, "id", None),
            collected_data=dict(chat_session.collected_data or {}),
        ):
            logger.warning(
                "confirmation_claim_downgraded_no_booking",
                extra={"session_id": session_id, "directive": directive},
            )
            assistant_message, directive = message_processor._build_confirmation_summary(
                chat_session, fd_map
            )

    if session_state_dirty:
        # A finished booking must stay finished. The graph keeps running after
        # _handle_booking_flow, and its in-memory copy of the session still
        # said ACTIVE, so the final save here wrote ACTIVE back over the
        # CONFIRMED that _execute_booking had persisted. The session then
        # looked like an unfinished booking and the customer was pushed into
        # booking a second appointment.
        #
        # Terminal states are one-way: never let a stale in-memory copy undo
        # a CONFIRMED/CANCELLED/EXPIRED session that is already on disk.
        try:
            _persisted = await crud_chat.get_chat_session(db, session_id)
            _persisted_status = str(
                getattr(_persisted, "session_status", "") or ""
            ).upper()
            _incoming_status = str(
                getattr(chat_session, "session_status", "") or ""
            ).upper()
            if _persisted_status in _TERMINAL_SESSION_STATUSES and (
                _incoming_status not in _TERMINAL_SESSION_STATUSES
            ):
                logger.info(
                    "Skipping stale session save: disk=%s memory=%s session=%s",
                    _persisted_status,
                    _incoming_status,
                    str(session_id)[:8],
                )
                chat_session.session_status = _persisted_status
                session_state_dirty = False
        except Exception as _status_exc:
            logger.warning("Terminal session status guard failed: %s", _status_exc)

    if session_state_dirty:
        await crud_chat.update_chat_session(db, session_id, chat_session)

    # Final authority on confirmation wording. Placed immediately before the
    # payload is built so it covers EVERY path above, including the ones that
    # return early.
    #
    # Observed failure: "Thanks Will. Your appointment is confirmed for
    # October 5th at 3 PM. We look forward to seeing you." was sent while the
    # OTP was still pending and no Booking row existed. The earlier guard
    # either did not run on that path or downgraded too late, and a customer
    # was told they were booked before verification. Nothing may say
    # "confirmed" unless a Booking row is actually on disk.
    # Final authority on confirmation wording, for the paths that fall through
    # to here. The early-return paths call the same helper themselves.
    _guarded = await _guard_confirmation_claim(
        db,
        session_id,
        assistant_message,
        directive,
        chat_session,
        field_meta,
        org,
        fd_map,
    )
    if _guarded is not None:
        assistant_message, directive, field_meta = _guarded

    # 3. Create ActionPayload to emulate traditional response
    action = ActionPayload(
        message=assistant_message,
        widget_directive=directive,
        field_meta=field_meta,
        is_terminal=False
    )
    await _persist_transcript_best_effort(
        db,
        session_id=session_id,
        user_message=_transcript_user_text, user_external_id=_inbound_id,
        assistant_message=action.message,
        directive=action.widget_directive or "TEXT_INPUT",
        intent=chat_session.current_intent,
    )
    return action
