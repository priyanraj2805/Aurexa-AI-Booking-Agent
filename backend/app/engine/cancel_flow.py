import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import BackgroundTasks
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.booking import Booking, TimeSlot
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.session import ChatSession
from app.schemas.agent import ActionPayload, ChatSessionState
from app.crud import chat as crud_chat
from app.crud import otp as otp_crud
from app.core import mail as mailer

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Windows fallback
    from backports.zoneinfo import ZoneInfo

CANCEL_FLOW_KEY = "_cancel_flow"
CANCEL_LOOKUP_DIRECTIVE = "REQUIRE_CANCEL_LOOKUP"
CANCELLED_DIRECTIVE = "CANCELLED_CONFIRMATION_CARD"


def _resolve_cancel_otp_channel(
    org_config: OrgConfig,
    channel: str = "",
) -> str:
    """Return ``'phone'`` or ``'email'`` based on admin OTP config and channel.

    A NULL flag means "schema default": email ON, phone OFF.
    """
    _raw_phone = getattr(org_config, "require_phone_otp", False)
    _raw_email = getattr(org_config, "require_otp", True)
    _use_phone = False if _raw_phone is None else bool(_raw_phone)
    _use_email = True if _raw_email is None else bool(_raw_email)
    _is_wa = str(channel).lower() in ("whatsapp", "whatsapp_cloud")
    if _is_wa and _use_phone:
        return "phone"
    if _use_email:
        return "email"
    if _use_phone:
        return "phone"
    return ""


def _otp_channel_label(otp_channel: str, org_config: OrgConfig) -> str:
    """Human-friendly label for the OTP delivery channel."""
    if otp_channel == "phone":
        return "WhatsApp"
    return "email"

# Booking-field keys (plus the stale runtime checkpoint) that must be pruned
# from persisted state_data whenever a session is reset for a new booking.
# crud.chat.update_chat_session MERGES state_data, so clearing the in-memory
# collected_data alone leaves stale values behind — the graph then resumes the
# old booking (skips field collection and goes straight to OTP/confirmation).
SESSION_RESET_REMOVE_KEYS = (
    "name",
    "email",
    "phone",
    "date",
    "time",
    "selected_slot_id",
    "selected_slot_start_utc",
    "locked_slot_id",
    "locked_slot_start_utc",
    "phone_country_code",
    "phone_raw_input",
    crud_chat.RUNTIME_CHECKPOINT_KEY,
    "active_directive_id",
    "_active_path_effects",
    "_otp_policy",
    "_otp_phone_policy",
    "_internal",
    CANCEL_FLOW_KEY,
)

_CANCEL_INTENT_PATTERNS = (
    r"\bcancel\b.{0,40}\b(?:booking|appointment|meeting|reservation|slot|session|schedule)\b",
    r"\b(?:booking|appointment|meeting|reservation|slot|session|schedule)\b.{0,40}\bcancel\b",
    r"\bi\s+(?:want|need|would\s+like)\s+to\s+cancel\b",
    r"\bi'?d\s+like\s+to\s+cancel\b",
    r"\bplease\s+cancel\b",
    r"\bcancel\s+my\b",
)

_RESEND_RE = re.compile(
    r"\b(resend|re-?send|recend|resent|recend otp|resent otp|send\s+(?:it|the\s+code|again)|new\s+code|didn'?t\s+(?:get|receiv(?:e|ed))|did\s+not\s+(?:get|receiv(?:e|ed))|haven'?t\s+(?:receiv(?:e|ed)|got|get)|hasn'?t\s+(?:receiv(?:e|ed)|got)|not\s+(?:receiv(?:e|ed)|got|getting)|never\s+(?:receiv(?:e|ed)|got|receive)|no\s+(?:otp|code)|not\s+received|waiting\s+for\s+(?:otp|code)|where.*otp|otp\s+not\s+received)\b",
    re.IGNORECASE,
)
_DIFFERENT_CONTACT_RE = re.compile(
    r"\bdifferent\s+(?:email|phone|number|contact)\b|\bwrong\s+(?:email|phone|number)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Messages that mean "abandon the cancel flow" — either to start a fresh
# booking or to drop the cancellation. The deterministic lookup gate runs
# before the LLM, so without these the widget loops on "enter an email".
_CANCEL_EXIT_PATTERNS = (
    # Explicit new-booking requests ("book a new appointment", "let's book").
    r"\b(?:book|make|schedule|arrange|reserve)\b.{0,40}\b(?:booking|appointment|meeting|slot|session|schedule)\b",
    r"\bi\s+(?:want|need|'?d\s+like)\s+to\s+book\b",
    r"\bstart\s+(?:a\s+)?new\s+(?:booking|appointment)\b",
    r"\bnew\s+(?:booking|appointment|meeting)\b",
    # Abandoning the cancellation ("never mind", "changed my mind", ...).
    r"\bnever\s*?mind\b",
    r"\bchanged\s+my\s+mind\b",
    r"\bno\s+worries\b",
    r"\b(?:let\s+it\s+be|leave\s+it\b|forget\s+(?:it|this|about\s+it)|skip\s+it|drop\s+it|scrap\s+it)\b",
    r"\b(?:no|don'?t|do\s+not)\s+(?:want|need)\s+(?:to\s+)?(?:cancel|find|look\s+up)\b",
    r"\bcancel\s+this\b(?!\s+(?:booking|appointment|meeting|reservation|slot|session|schedule))",
    r"\b(?:exit|quit|abort)\b",
    r"\bit(?:'s| is)?\s+(?:ok(?:ay)?|fine|alright|all\s+right)\b",
)

_FIND_BOOKING_RE = re.compile(
    r"\b(?:find|look\s+up|search|check|track)\b.{0,30}\b(?:my\s+)?(?:booking|appointment|meeting|reservation)\b",
    re.IGNORECASE,
)

_EMAIL_IN_TEXT_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

# Clear "we are talking about something else now" signals that must release a
# stuck cancel lookup. Wording that means "keep going" is handled separately so
# a half-typed contact value never gets released.
_CANCEL_RESUME_RE = re.compile(
    r"\b(?:continue|proceed|keep\s+going|go\s+on|carry\s+on|resume|as\s+i\s+said|"
    r"like\s+i\s+said|tell\s+you|that\s+is|its\s+my\s+email|my\s+(?:email|phone|mobile|number)\s+is)\b",
    re.IGNORECASE,
)

_CANCEL_TOPIC_CHANGE_PATTERNS = (
    # New booking intent (broadened beyond _CANCEL_EXIT_PATTERNS: those require
    # an explicit booking noun, which misses "i want an appointment tomorrow").
    r"\b(?:book|schedule|reserve|arrange)\b",
    r"\b(?:reschedule|move|change|shift)\b.{0,30}\b(?:booking|appointment|meeting|slot|time)\b",
    # Other transactional intents that are not this cancel lookup.
    r"\b(?:available|availability|slots?|timings?)\b.{0,30}\b(?:today|tomorrow|available|free|slots?)\b",
    r"\bwhat\s+(?:time|slot|day|days)\b",
    r"\bhow\s+(?:much|price|cost|long)\b",
    # A greeting/acknowledgement storm means the user is not entering a contact.
    r"^\s*(?:hi|hey|hello|yo|thanks|thank\s+you|ok|okay|k|cool|nice|great|awesome|got\s+it|alright)\b",
)

_CANCEL_LLM_ANALYSIS_TIMEOUT_SECONDS = 10.0

_CANCEL_LOOKUP_META = {
    "name": "cancel_lookup",
    "label": "Email or Phone",
    "type": "text",
    "icon": "🗓️",
    "placeholder": "you@example.com or +91 98765 43210",
    "description": "Enter the email or phone number used for the booking.",
}


def is_cancel_intent(
    message_text: str, llm_user_intent: Optional[str] = None
) -> bool:
    """Heuristic (keyword) + LLM intent fallback for cancel requests."""
    if str(llm_user_intent or "").strip().lower() == "cancel":
        return True
    text = str(message_text or "").strip()
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _CANCEL_INTENT_PATTERNS)


def is_cancel_exit_request(message_text: str) -> bool:
    """True when the user wants to abandon the cancel flow (new booking,
    "never mind", etc.). Keeps the deterministic lookup gate from trapping
    users who are not entering a contact value."""
    text = str(message_text or "").strip()
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _CANCEL_EXIT_PATTERNS)


def is_cancel_topic_change(message_text: str) -> bool:
    """True when a turn inside the cancel lookup is clearly a different topic.

    The lookup stage is waiting for an email or phone number. If the user sends
    a knowledge question ("do you offer hair cuts?") or restarts another
    booking concern, the cancel flow must stand down instead of answering
    inside the flow and re-asking for the contact.

    Deliberately conservative: only fires on strong, unambiguous signals so a
    genuine "my email is ..." answer never gets released mid-lookup.
    """
    text = str(message_text or "").strip()
    if not text:
        return False
    # A contact value, cancel intent, or resume wording always stays in flow.
    if parse_lookup_value(text) or extract_contact_from_text(text):
        return False
    if is_cancel_intent(text) or is_cancel_exit_request(text) or _FIND_BOOKING_RE.search(text):
        return False
    if _CANCEL_RESUME_RE.search(text):
        return False

    try:
        from app.engine.agent.graph import _is_potential_knowledge_question

        if _is_potential_knowledge_question(text):
            return True
    except Exception:
        pass

    return any(
        re.search(pattern, text, re.IGNORECASE) for pattern in _CANCEL_TOPIC_CHANGE_PATTERNS
    )


def reset_for_new_booking(chat_session: ChatSessionState) -> None:
    """Exit the cancel flow and restore a clean START_BOOKING session.

    Keeps only the field-definitions snapshot so the booking flow resumes
    from a blank slate without losing org config. The stale runtime
    checkpoint is dropped (SESSION_RESET_REMOVE_KEYS handles the pruning
    at persist time) so session recovery can never resurrect the old
    booking's collected data.
    """
    keep_keys = {
        crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY,
    }
    collected = dict(chat_session.collected_data or {})
    chat_session.collected_data = {
        key: value for key, value in collected.items() if key in keep_keys
    }
    chat_session.missing_fields = []
    chat_session.current_intent = "START_BOOKING"
    chat_session.session_status = "ACTIVE"
    chat_session.otp_verified = False
    chat_session.phone_otp_verified = False
    chat_session.pending_cancel = False


def extract_contact_from_text(text: str) -> Optional[Tuple[str, str]]:
    """Find an email or phone number embedded in free text.

    Handles "my email is x@y.com", "call 98765 43210", etc. OTP codes
    (6 digits) are deliberately NOT treated as contacts.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    match = _EMAIL_IN_TEXT_RE.search(cleaned)
    if match:
        return ("email", match.group(0))
    digits = re.sub(r"\D", "", cleaned)
    if 10 <= len(digits) <= 15:
        return ("phone", digits)
    return None


async def _llm_cancel_flow_analysis(
    message_text: str,
    stage: str,
    flow: Dict[str, Any],
) -> Dict[str, Any]:
    """LLM analysis of a message that the deterministic cancel-flow gate
    could not classify (smalltalk, questions, mixed phrasing).

    The model classifies intent AND writes the conversational reply in one
    call. Never raises: on any failure a safe NEUTRAL fallback is returned
    so the flow never blocks.
    """
    import asyncio
    import json as _json
    from app.engine import message_processor

    stage_label = {
        "lookup": (
            "The widget is asking the customer for the EMAIL or PHONE they used "
            "for the appointment so it can find and cancel it."
        ),
        "otp": (
            "The widget found the appointment and is waiting for the customer to "
            "enter the 6-digit verification code that was emailed to them."
        ),
    }.get(stage, "An appointment-cancellation flow is in progress.")

    prompt = f"""Analyze the user message in an appointment-cancellation flow inside a booking widget.

CANCEL_FLOW_STAGE: {stage}
CONTEXT: {stage_label}
BOOKING_FOUND: {"yes, awaiting verification code" if stage == "otp" else "not yet looked up"}
USER_MESSAGE: "{message_text}"

Classify the user's intent. Use ONLY the message's actual meaning:

- classification "CONTACT_VALUE": the message contains a NEW email or phone
  number to use for the lookup (e.g. "my email is x@y.com", "try 98765 43210").
- classification "EXIT_FLOW": the user wants to stop the cancellation flow —
  to start a new booking, or to abandon the cancellation entirely.
  Examples: "forget it", "let's book a new appointment", "never mind",
  "I don't want to cancel anymore".
- classification "RESUME_FLOW": the user confirms they want to continue the
  cancellation flow. Examples: "yes", "ok let's continue", "find my booking".
- classification "SMALLTALK": anything else — greetings, thanks, unrelated
  questions, small talk.

If classification is SMALLTALK, write "reply": a warm 1-2 sentence assistant
reply that answers the user naturally. Do NOT mention the email/phone or code
in the reply itself — the widget already shows the input field.

Return ONLY valid JSON:
{{"classification": "CONTACT_VALUE|EXIT_FLOW|RESUME_FLOW|SMALLTALK",
  "confidence": 0.0,
  "reply": "..."}}
"""
    try:
        result = await asyncio.wait_for(
            message_processor.llm_engine._call_llm(
                prompt,
                system_prompt=(
                    "You are a strict booking-cancellation intent classifier. "
                    "Output JSON only."
                ),
                json_mode=True,
            ),
            timeout=_CANCEL_LLM_ANALYSIS_TIMEOUT_SECONDS,
        )
        payload = _json.loads(str(result or ""))
        classification = str(
            payload.get("classification", "SMALLTALK")
        ).upper()
        if classification not in (
            "CONTACT_VALUE",
            "EXIT_FLOW",
            "RESUME_FLOW",
            "SMALLTALK",
        ):
            classification = "SMALLTALK"
        return {
            "classification": classification,
            "confidence": float(payload.get("confidence", 0.0)),
            "reply": str(payload.get("reply") or "").strip(),
        }
    except Exception:
        logger.warning(
            "Cancel-flow LLM analysis failed, using fallback",
            exc_info=True,
        )
        return {
            "classification": "SMALLTALK",
            "confidence": 0.0,
            "reply": "",
        }


def parse_lookup_value(text: str) -> Optional[Tuple[str, str]]:
    """Return ("email", value) or ("phone", digits) if the text looks like one."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    if _EMAIL_RE.match(cleaned):
        return ("email", cleaned)
    digits = re.sub(r"\D", "", cleaned)
    if 10 <= len(digits) <= 15:
        return ("phone", digits)
    return None


def get_cancel_flow(chat_session: ChatSessionState) -> Optional[Dict[str, Any]]:
    data = chat_session.collected_data
    if not isinstance(data, dict):
        return None
    flow = data.get(CANCEL_FLOW_KEY)
    return flow if isinstance(flow, dict) else None


def set_cancel_flow(
    chat_session: ChatSessionState, **updates: Any
) -> Dict[str, Any]:
    data = dict(chat_session.collected_data or {})
    flow = data.get(CANCEL_FLOW_KEY)
    if not isinstance(flow, dict):
        flow = {}
    flow.update(updates)
    data[CANCEL_FLOW_KEY] = flow
    chat_session.collected_data = data
    return flow


def clear_cancel_flow(chat_session: ChatSessionState) -> None:
    data = chat_session.collected_data
    if isinstance(data, dict):
        data.pop(CANCEL_FLOW_KEY, None)


def _lookup_action(
    message: str, *, validation_error: str = ""
) -> ActionPayload:
    meta = dict(_CANCEL_LOOKUP_META)
    if validation_error:
        meta["validation_error"] = validation_error
    return ActionPayload(
        message=message, widget_directive=CANCEL_LOOKUP_DIRECTIVE, field_meta=meta
    )


def _is_resend_request(message_text: str) -> bool:
    text = str(message_text or "").strip()
    if not text:
        return False
    if _RESEND_RE.search(text):
        return True
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
        )
    )
    return bool(has_otp and has_neg)


def _is_different_contact_request(message_text: str) -> bool:
    text = str(message_text or "").strip()
    if not text:
        return False
    return bool(_DIFFERENT_CONTACT_RE.search(text))


def _org_timezone(org_config: OrgConfig):
    tz_name = getattr(org_config, "timezone", None) or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _format_slot_label(
    start_time: Optional[datetime], org_config: OrgConfig
) -> str:
    if not start_time:
        return "your appointment"
    local = start_time.astimezone(_org_timezone(org_config))
    hour_12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    label = local.strftime("%A, %B %d")
    return f"{label} at {hour_12}:{local.minute:02d} {ampm}"


async def _save_transcript(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    assistant_message: str,
    directive: str,
    intent: Optional[str] = None,
) -> None:
    try:
        await crud_chat.save_message(db, session_id, "user", user_message)
        metadata: Dict[str, Any] = {"directive": directive}
        if intent:
            metadata["intent"] = intent
        await crud_chat.save_message(
            db, session_id, "agent", assistant_message, metadata=metadata
        )
    except Exception:
        logger.warning("Failed to persist cancel-flow transcript", exc_info=True)


async def _persist_session(
    db: AsyncSession,
    chat_session: ChatSessionState,
    *,
    remove_keys: Tuple[str, ...] = (),
) -> None:
    """Persist ChatSessionState fields onto the ChatSession row.

    Unlike crud.chat.update_chat_session, this honors explicit removals
    (state_data JSONB is merged by update_chat_session, never pruned).
    """
    try:
        import uuid as _uuid

        result = await db.execute(
            select(ChatSession).where(ChatSession.id == _uuid.UUID(chat_session.session_id))
        )
        db_session = result.scalars().first()
    except Exception:
        return
    if db_session is None:
        return
    from sqlalchemy.orm.attributes import flag_modified

    incoming = dict(chat_session.collected_data or {})
    existing = dict(db_session.state_data or {})
    for key, value in existing.items():
        if key not in incoming and key not in remove_keys:
            incoming[key] = value
    for key in remove_keys:
        incoming.pop(key, None)
    db_session.state_data = incoming
    db_session.missing_fields = list(chat_session.missing_fields or [])
    db_session.current_intent = chat_session.current_intent
    db_session.session_status = chat_session.session_status
    db_session.otp_verified = bool(chat_session.otp_verified)
    db_session.phone_otp_verified = bool(getattr(chat_session, "phone_otp_verified", False))
    db_session.pending_cancel = bool(chat_session.pending_cancel)
    flag_modified(db_session, "state_data")
    await db.commit()


async def handle_active_cancel_flow(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Optional[ActionPayload]:
    """Pre-graph gate: deterministically handle messages while a cancel flow is active.

    Returns None when no cancel flow is active (normal booking path continues)
    OR when the user abandons the cancel flow (the flow is cleared so the
    message can be processed as a normal booking request downstream).
    """
    flow = get_cancel_flow(chat_session)
    if flow is None:
        return None
    stage = str(flow.get("stage") or "lookup")
    if stage == "lookup":
        return await _handle_lookup_stage(
            db, org, org_config, chat_session, message_text, background_tasks
        )
    if stage == "otp":
        return await _handle_otp_stage(
            db, org, org_config, chat_session, message_text, background_tasks
        )
    # Unknown stage: reset to lookup
    set_cancel_flow(chat_session, stage="lookup", otp_verified=False)
    await _persist_session(db, chat_session)
    action = _lookup_action(
        "Let's find your appointment. Please enter the email or phone number you used."
    )
    await _save_transcript(
        db,
        chat_session.session_id,
        message_text,
        action.message,
        action.widget_directive,
        intent="CANCEL_BOOKING",
    )
    return action


async def start_cancel_flow(
    db: AsyncSession, chat_session: ChatSessionState, message_text: str
) -> ActionPayload:
    """Post-graph hook: initialise the lookup stage for a detected cancel intent."""
    set_cancel_flow(chat_session, stage="lookup", otp_verified=False)
    chat_session.pending_cancel = True
    await _persist_session(db, chat_session)
    return _lookup_action(
        "Sure — I can help with that. Please enter the email or phone number you used for the appointment."
    )


async def _find_upcoming_booking(
    db: AsyncSession, org: Organization, kind: str, value: str
) -> Tuple[Optional[Booking], Optional[datetime]]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Booking, TimeSlot.start_time)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .where(Booking.org_id == org.id, TimeSlot.start_time >= now)
    )
    if kind == "email":
        stmt = stmt.where(func.lower(Booking.customer_email) == value.lower())
    else:
        stmt = stmt.where(Booking.customer_phone.ilike(f"%{value[-10:]}%"))
    stmt = stmt.order_by(TimeSlot.start_time.asc())
    rows = (await db.execute(stmt)).all()
    for booking, start_time in rows:
        if kind == "email":
            return booking, start_time
        stored_digits = re.sub(r"\D", "", str(booking.customer_phone or ""))
        if stored_digits and (
            stored_digits == value or stored_digits.endswith(value[-10:])
        ):
            return booking, start_time
    return None, None


async def _fetch_booking(
    db: AsyncSession, org: Organization, booking_id: str
) -> Tuple[Optional[Booking], Optional[datetime]]:
    try:
        booking_uuid = uuid.UUID(booking_id)
    except Exception:
        return None, None
    row = (
        await db.execute(
            select(Booking, TimeSlot.start_time)
            .join(TimeSlot, Booking.slot_id == TimeSlot.id)
            .where(Booking.id == booking_uuid, Booking.org_id == org.id)
        )
    ).first()
    if not row:
        return None, None
    return row[0], row[1]


async def _handle_lookup_stage(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks],
) -> Optional[ActionPayload]:
    session_id = chat_session.session_id
    parsed = parse_lookup_value(message_text)
    if parsed is None:
        # A contact value embedded in free text ("my email is x@y.com").
        embedded = extract_contact_from_text(message_text)
        if embedded is not None:
            return await _handle_lookup_stage(
                db, org, org_config, chat_session, embedded[1], background_tasks
            )
        # User wants out: start a new booking or abandon the cancellation.
        # Clear the flow and return None so the message is processed as a
        # normal (LLM-analyzed) booking turn downstream.
        if is_cancel_exit_request(message_text):
            reset_for_new_booking(chat_session)
            await _persist_session(
                db, chat_session, remove_keys=(CANCEL_FLOW_KEY, *SESSION_RESET_REMOVE_KEYS)
            )
            logger.info(
                "Cancel flow exited by user request",
                extra={"session_id": session_id, "message": message_text[:120]},
            )
            return None
        if _FIND_BOOKING_RE.search(message_text):
            action = _lookup_action(
                "Let's find your appointment. Please enter the email or phone number you used."
            )
            await _save_transcript(
                db, session_id, message_text, action.message, action.widget_directive,
                intent="CANCEL_BOOKING",
            )
            return action

        # Deterministic topic-change escape. A cancel lookup asks for an email or
        # phone number, but the user may ignore it and ask something unrelated
        # (a knowledge question, a new booking, a reschedule). Falling through to
        # the LLM smalltalk branch answered the question and then KEPT the lookup
        # prompt, so the conversation looped while _cancel_flow stayed armed and
        # kept intercepting later turns. Release the flow and return None so the
        # engine handles the turn normally; the user can re-initiate cancel.
        if is_cancel_topic_change(message_text):
            clear_cancel_flow(chat_session)
            await _persist_session(
                db, chat_session, remove_keys=(CANCEL_FLOW_KEY,)
            )
            logger.info(
                "Cancel flow released by topic change",
                extra={"session_id": session_id, "message": message_text[:120]},
            )
            return None

        # Everything else goes to the LLM: it may spot a contact value we
        # missed, detect an exit/resume intent, or answer smalltalk naturally.
        # The flow state (_cancel_flow) is untouched here, so the lookup
        # resumes exactly where it was when the user returns to it.
        analysis = await _llm_cancel_flow_analysis(
            message_text, stage="lookup", flow=get_cancel_flow(chat_session) or {}
        )
        classification = analysis.get("classification", "SMALLTALK")
        if classification == "CONTACT_VALUE":
            embedded = extract_contact_from_text(message_text)
            if embedded is not None:
                return await _handle_lookup_stage(
                    db, org, org_config, chat_session, embedded[1], background_tasks
                )
            classification = "SMALLTALK"
        if classification == "EXIT_FLOW":
            reset_for_new_booking(chat_session)
            await _persist_session(
                db, chat_session, remove_keys=(CANCEL_FLOW_KEY, *SESSION_RESET_REMOVE_KEYS)
            )
            logger.info(
                "Cancel flow exited via LLM analysis",
                extra={"session_id": session_id, "message": message_text[:120]},
            )
            return None
        if classification == "RESUME_FLOW":
            action = _lookup_action(
                "Sure — let's keep going. Please enter the email or phone number you used for the appointment."
            )
            await _save_transcript(
                db, session_id, message_text, action.message, action.widget_directive,
                intent="CANCEL_BOOKING",
            )
            return action

        reply = str(analysis.get("reply") or "").strip() or (
            "I'm sorry, I didn't quite get that. To find your appointment, "
            "please enter the email or phone number you used — or say "
            "'book a new appointment' to start a fresh booking."
        )
        action = _lookup_action(reply)
        await _save_transcript(
            db, session_id, message_text, action.message, action.widget_directive,
            intent="CANCEL_BOOKING",
        )
        return action

    kind, value = parsed
    booking, start_time = await _find_upcoming_booking(db, org, kind, value)
    if booking is None:
        display = value if kind == "email" else f"+{value}"
        action = _lookup_action(
            f"We couldn't find an upcoming appointment for **{display}**. "
            "Please double-check and try again, or start a new booking if you meant to book."
        )
        await _save_transcript(
            db, session_id, message_text, action.message, action.widget_directive,
            intent="CANCEL_BOOKING",
        )
        return action

    return await _send_cancel_otp(
        db, org, org_config, chat_session, booking, start_time, background_tasks
    )


async def _send_cancel_otp(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    booking: Booking,
    start_time: Optional[datetime],
    background_tasks: Optional[BackgroundTasks],
) -> ActionPayload:
    session_id = chat_session.session_id
    from app.crud.otp import invalidate_otp_delivery
    from app.core.otp_delivery import delivery_error, delivery_succeeded
    from app.core.config import settings as _cancel_otp_settings

    _channel = _resolve_cancel_otp_channel(
        org_config,
        channel=str(getattr(chat_session, "channel", "") or ""),
    )
    if not _channel:
        # Admin disabled all OTP (email OFF + phone OFF): cancel proceeds
        # WITHOUT a code. Strictness applies only when admin enables OTP.
        logger.info("cancel OTP skipped (all OTP disabled) for org %s", org.id)
        set_cancel_flow(
            chat_session,
            stage="otp",
            booking_id=str(booking.id),
            email=booking.customer_email,
            phone=str(booking.customer_phone or ""),
            customer_name=booking.customer_name,
            slot_label=_format_slot_label(start_time, org_config),
            otp_verified=True,
        )
        chat_session.pending_cancel = True
        await _persist_session(db, chat_session)
        return await _execute_cancellation(
            db, org, org_config, chat_session,
            get_cancel_flow(chat_session) or {}, background_tasks,
        )
    try:
        code = await otp_crud.generate_otp(db, session_id, channel=_channel, resend=False)
    except Exception:
        logger.exception("cancel OTP generation failed for session %s", session_id)
        action = _lookup_action(
            "I couldn't send the verification code right now. Please try again in a moment."
        )
        await _save_transcript(
            db, session_id, "", action.message, action.widget_directive,
            intent="CANCEL_BOOKING",
        )
        return action

    slot_label = _format_slot_label(start_time, org_config)
    set_cancel_flow(
        chat_session,
        stage="otp",
        booking_id=str(booking.id),
        email=booking.customer_email,
        phone=str(booking.customer_phone or ""),
        customer_name=booking.customer_name,
        slot_label=slot_label,
        otp_verified=False,
    )
    chat_session.pending_cancel = True
    await _persist_session(db, chat_session)

    idem_key = f"otp:cancel:{booking.id}:{session_id}"
    _phone = str(
        booking.customer_phone
        or chat_session.collected_data.get("phone")
        or ""
    ).strip()
    delivery_channel = _channel
    delivery_result = None
    if _channel == "phone":
        if not _phone:
            _phone = str(getattr(chat_session, "channel_user_id", "") or "").strip()
        from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider
        _provider = WhatsAppProvider()
        try:
            delivery_result = await _provider.send_otp(_phone, code, str(session_id))
        except Exception as exc:
            delivery_result = {"success": False, "error": str(exc)}
        if not delivery_succeeded(
            delivery_result,
            allow_mock=bool(getattr(_cancel_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
        ) and getattr(org_config, "require_otp", False) and booking.customer_email:
            email_kwargs = dict(
                to_email=booking.customer_email,
                code=code,
                org_id=org.id,
                idempotency_key=idem_key,
            )
            try:
                delivery_result = await mailer.send_otp_email(**email_kwargs)
            except Exception as exc:
                delivery_result = {"success": False, "error": str(exc)}
            if delivery_succeeded(
                delivery_result,
                allow_mock=bool(getattr(_cancel_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
            ):
                delivery_channel = "email"
    else:
        if not booking.customer_email:
            await invalidate_otp_delivery(db, session_id, "email", context_key=idem_key)
            clear_cancel_flow(chat_session)
            chat_session.pending_cancel = False
            await _persist_session(db, chat_session, remove_keys=(CANCEL_FLOW_KEY,))
            action = _lookup_action("I couldn't send the verification code because no email is available.")
            await _save_transcript(db, session_id, "", action.message, action.widget_directive, intent="CANCEL_BOOKING")
            return action
        email_kwargs = dict(
            to_email=booking.customer_email,
            code=code,
            org_id=org.id,
            idempotency_key=idem_key,
        )
        try:
            delivery_result = await mailer.send_otp_email(**email_kwargs)
        except Exception as exc:
            delivery_result = {"success": False, "error": str(exc)}

    if not delivery_succeeded(
        delivery_result,
        allow_mock=bool(getattr(_cancel_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
    ):
        await invalidate_otp_delivery(
            db,
            session_id,
            delivery_channel,
            context_key=idem_key,
        )
        clear_cancel_flow(chat_session)
        chat_session.pending_cancel = False
        await _persist_session(db, chat_session, remove_keys=(CANCEL_FLOW_KEY,))
        action = _lookup_action(
            "I couldn't send the verification code right now. Please try again in a moment."
        )
        await _save_transcript(
            db,
            session_id,
            "",
            action.message,
            action.widget_directive,
            intent="CANCEL_BOOKING",
        )
        return action
    _channel = delivery_channel

    policy_meta: Dict[str, Any] = {}
    try:
        policy_meta = await otp_crud.get_otp_policy_metadata(db, session_id, channel=_channel)
    except Exception:
        pass
    meta = dict(policy_meta)
    meta["cancel_flow"] = True
    meta["channel"] = _channel
    if _channel == "phone":
        # Star-free mask (see user_visible_text.mask_phone_for_display):
        # "***" masks were eaten by WhatsApp bold formatting, leaking digits
        # back together and leaving literal "**" in customer messages.
        from app.engine.user_visible_text import (
            mask_phone_for_display as _mask_phone,
        )

        _masked = _mask_phone(_phone)
        _via = "WhatsApp"
        message = (
            f"I found your appointment — **{slot_label}**. To cancel it, "
            f"I've sent a verification code to **{_masked}** via {_via}. "
            "Please enter the code below to confirm."
        )
    else:
        message = (
            f"I found your appointment — **{slot_label}**. To cancel it, "
            f"I've sent a verification code to **{booking.customer_email}**. "
            "Please enter the code below to confirm."
        )
    action = ActionPayload(
        message=message, widget_directive="REQUIRE_OTP_MODAL", field_meta=meta
    )
    await _save_transcript(
        db, session_id, "", action.message, action.widget_directive,
        intent="CANCEL_BOOKING",
    )
    return action


async def _handle_otp_stage(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks],
) -> Optional[ActionPayload]:
    flow = get_cancel_flow(chat_session)
    if flow.get("otp_verified"):
        return await _execute_cancellation(
            db,
            org,
            org_config,
            chat_session,
            flow,
            background_tasks,
            user_message=message_text,
        )

    text = str(message_text or "").strip()
    if _is_different_contact_request(text):
        set_cancel_flow(chat_session, stage="lookup", otp_verified=False)
        await _persist_session(db, chat_session)
        action = _lookup_action(
            "No problem — please enter the email or phone number you used for the appointment."
        )
        await _save_transcript(
            db, chat_session.session_id, message_text, action.message,
            action.widget_directive, intent="CANCEL_BOOKING",
        )
        return action

    if _is_resend_request(text):
        return await _handle_cancel_resend(
            db,
            org,
            org_config,
            chat_session,
            flow,
            background_tasks,
            user_message=message_text,
        )

    # User typed the 6-digit code in the chat instead of the code box.
    # On WhatsApp there is no code box — verify directly. On web, re-show the modal.
    if re.fullmatch(r"\d{6}", text):
        # whatsapp_cloud has no code box either — same direct-verify path.
        _otp_channel = _resolve_cancel_otp_channel(
            org_config,
            channel=str(getattr(chat_session, "channel", "") or ""),
        )
        _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
        if _is_whatsapp:
            try:
                _code = text.strip()
                # Try the admin-selected channel first, then the other (if also enabled)
                _channel_order = []
                if _otp_channel:
                    _channel_order.append(_otp_channel)
                for _ch in ("email", "phone"):
                    if _ch != _otp_channel and _ch not in _channel_order:
                        _channel_order.append(_ch)
                _verified = False
                for _ch in _channel_order:
                    try:
                        if await otp_crud.verify_otp(db, chat_session.session_id, code=_code, channel=_ch):
                            _verified = True
                            _ver_channel = _ch
                            break
                    except ValueError:
                        # Too many attempts etc. — surface as invalid
                        pass
                if _verified:
                    flow["otp_verified"] = True
                    set_cancel_flow(chat_session, stage="otp", otp_verified=True, booking_id=str(flow.get("booking_id") or ""), email=str(flow.get("email") or ""))
                    await _persist_session(db, chat_session)
                    return await _execute_cancellation(
                        db,
                        org,
                        org_config,
                        chat_session,
                        flow,
                        background_tasks,
                        user_message=message_text,
                    )
                # Invalid code — show error and re-prompt, don't loop silently
                _dest_label = (
                    str(flow.get("phone") or "your phone")
                    if _otp_channel == "phone"
                    else str(flow.get("email") or "your email address")
                )
                meta: Dict[str, Any] = {"cancel_flow": True, "validation_error": "That code is incorrect or has expired. Please try again."}
                try:
                    meta.update(await otp_crud.get_otp_policy_metadata(db, chat_session.session_id, channel=_otp_channel))
                except Exception:
                    pass
                action = ActionPayload(
                    message=f"That code is incorrect or has expired. Please enter the correct code sent to **{_dest_label}**.",
                    widget_directive="REQUIRE_OTP_MODAL",
                    field_meta=meta,
                )
                await _save_transcript(db, chat_session.session_id, message_text, action.message, action.widget_directive, intent="CANCEL_BOOKING")
                return action
            except Exception as _e:
                logger.warning(f"Cancel OTP chat verify failed: {_e}")
        # Web: re-show the modal deterministically — no LLM call needed.
        _dest_label = (
            str(flow.get("phone") or "your phone")
            if _otp_channel == "phone"
            else str(flow.get("email") or "your email address")
        )
        meta: Dict[str, Any] = {"cancel_flow": True}
        try:
            meta.update(
                await otp_crud.get_otp_policy_metadata(db, chat_session.session_id, channel=_otp_channel)
            )
        except Exception:
            pass
        action = ActionPayload(
            message=f"Please enter the verification code sent to **{_dest_label}** in the code box below to confirm the cancellation.",
            widget_directive="REQUIRE_OTP_MODAL",
            field_meta=meta,
        )
        await _save_transcript(
            db, chat_session.session_id, message_text, action.message,
            action.widget_directive, intent="CANCEL_BOOKING",
        )
        return action

    # A contact value (plain or embedded in free text) → switch back to
    # the lookup stage with that value.
    contact = parse_lookup_value(text) or extract_contact_from_text(text)
    if contact is not None:
        set_cancel_flow(chat_session, stage="lookup", otp_verified=False)
        await _persist_session(db, chat_session)
        return await _handle_lookup_stage(
            db, org, org_config, chat_session, contact[1], background_tasks
        )

    # User abandons the cancellation mid-OTP ("never mind", "book a new
    # appointment", ...). Clear the flow and continue as a normal turn.
    if is_cancel_exit_request(text):
        reset_for_new_booking(chat_session)
        await _persist_session(db, chat_session, remove_keys=(CANCEL_FLOW_KEY, *SESSION_RESET_REMOVE_KEYS))
        logger.info(
            "Cancel flow exited during OTP stage",
            extra={"session_id": chat_session.session_id, "message": text[:120]},
        )
        return None

    # Smalltalk/questions while awaiting the code → LLM answers naturally,
    # the flow stays in the OTP stage, and the code modal is re-shown.
    analysis = await _llm_cancel_flow_analysis(
        message_text, stage="otp", flow=flow
    )
    classification = analysis.get("classification", "SMALLTALK")
    if classification == "CONTACT_VALUE":
        contact = extract_contact_from_text(text)
        if contact is not None:
            set_cancel_flow(chat_session, stage="lookup", otp_verified=False)
            await _persist_session(db, chat_session)
            return await _handle_lookup_stage(
                db, org, org_config, chat_session, contact[1], background_tasks
            )
        classification = "SMALLTALK"
    if classification == "EXIT_FLOW":
        reset_for_new_booking(chat_session)
        await _persist_session(db, chat_session, remove_keys=(CANCEL_FLOW_KEY, *SESSION_RESET_REMOVE_KEYS))
        logger.info(
            "Cancel flow exited during OTP stage via LLM analysis",
            extra={"session_id": chat_session.session_id, "message": text[:120]},
        )
        return None
    if classification == "RESUME_FLOW":
        _dest_label = (
            str(flow.get("phone") or "your phone")
            if _otp_channel == "phone"
            else str(flow.get("email") or "your email address")
        )
        action = ActionPayload(
            message=f"Sure — please enter the verification code sent to **{_dest_label}** to confirm the cancellation.",
            widget_directive="REQUIRE_OTP_MODAL",
            field_meta={"cancel_flow": True},
        )
        await _save_transcript(
            db, chat_session.session_id, message_text, action.message,
            action.widget_directive, intent="CANCEL_BOOKING",
        )
        return action

    _dest_label = (
        str(flow.get("phone") or "your phone")
        if _otp_channel == "phone"
        else str(flow.get("email") or "your email address")
    )
    reply = str(analysis.get("reply") or "").strip() or (
        f"I'm sorry, I didn't quite get that. To cancel, please enter the "
        f"verification code I sent to **{_dest_label}** — or say 'book a new "
        "appointment' to start a fresh booking."
    )
    meta: Dict[str, Any] = {"cancel_flow": True}
    try:
        meta.update(await otp_crud.get_otp_policy_metadata(db, chat_session.session_id, channel=_otp_channel))
    except Exception:
        pass
    action = ActionPayload(
        message=reply,
        widget_directive="REQUIRE_OTP_MODAL",
        field_meta=meta,
    )
    await _save_transcript(
        db, chat_session.session_id, message_text, action.message,
        action.widget_directive, intent="CANCEL_BOOKING",
    )
    return action


async def _handle_cancel_resend(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    flow: Dict[str, Any],
    background_tasks: Optional[BackgroundTasks],
    *,
    user_message: str = "resend",
) -> ActionPayload:
    session_id = chat_session.session_id
    from app.crud.otp import invalidate_otp_delivery
    from app.core.otp_delivery import delivery_error, delivery_succeeded
    from app.core.config import settings as _cancel_resend_settings

    email = str(flow.get("email") or "")
    booking_id = str(flow.get("booking_id") or "")
    _otp_channel = _resolve_cancel_otp_channel(
        org_config,
        channel=str(getattr(chat_session, "channel", "") or ""),
    )
    meta: Dict[str, Any] = {"cancel_flow": True, "channel": _otp_channel}
    try:
        code = await otp_crud.generate_otp(db, session_id, channel=_otp_channel, resend=True)
        policy_meta = await otp_crud.get_otp_policy_metadata(db, session_id, channel=_otp_channel)
        resend_attempt = otp_crud.OTP_RESEND_CAP - int(
            policy_meta.get("resends_left", 0)
        )
        idem_key = f"otp:cancel:{booking_id}:{session_id}:resend:{resend_attempt}"
        _phone = str(
            flow.get("phone")
            or chat_session.collected_data.get("phone")
            or getattr(chat_session, "channel_user_id", "")
            or ""
        ).strip()
        if _otp_channel == "phone":
            from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider
            _provider = WhatsAppProvider()
            _result = await _provider.send_otp(_phone, code, str(session_id))
            if not delivery_succeeded(
                _result,
                allow_mock=bool(getattr(_cancel_resend_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
            ):
                await invalidate_otp_delivery(db, session_id, "phone", context_key=idem_key)
                raise RuntimeError(delivery_error(_result))
            _dest = _phone or "your phone"
            _via = _otp_channel_label("phone", org_config)
            message = f"I've sent a new verification code to **{_dest}** via {_via}."
        else:
            if not email:
                await invalidate_otp_delivery(db, session_id, "email", context_key=idem_key)
                raise ValueError("Email address is required")
            email_kwargs = dict(
                to_email=email,
                code=code,
                org_id=org.id,
                idempotency_key=idem_key,
            )
            _result = await mailer.send_otp_email(**email_kwargs)
            if not delivery_succeeded(
                _result,
                allow_mock=bool(getattr(_cancel_resend_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
            ):
                await invalidate_otp_delivery(db, session_id, "email", context_key=idem_key)
                raise RuntimeError(delivery_error(_result))
            message = f"I've sent a new verification code to **{email}**. Please check your inbox."
        meta.update(policy_meta)
        directive = "REQUIRE_OTP_MODAL"
    except ValueError as otp_err:
        try:
            meta.update(
                await otp_crud.get_otp_policy_metadata(db, session_id)
            )
        except Exception:
            pass
        message = str(otp_err)
        directive = "REQUIRE_OTP_MODAL"
    except Exception:
        logger.exception("cancel OTP resend failed for session %s", session_id)
        message = "I'm having trouble sending the code. Please try again in a moment."
        directive = "REQUIRE_OTP_MODAL"
    action = ActionPayload(
        message=message, widget_directive=directive, field_meta=meta
    )
    await _save_transcript(
        db,
        session_id,
        user_message,
        action.message,
        directive,
        intent="CANCEL_BOOKING",
    )
    return action


async def _execute_cancellation(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    flow: Dict[str, Any],
    background_tasks: Optional[BackgroundTasks],
    *,
    user_message: str = "",
) -> ActionPayload:
    from app.crud.booking import cancel_booking
    from app.core.audit import log_audit_event, log_audit_event_async
    from app.core.integrations.calendar_providers.factory import get_provider
    from app.core.mail import send_cancellation_email
    from app.schemas.audit import AuditAction

    session_id = chat_session.session_id
    booking_id = str(flow.get("booking_id") or "")

    # STRICT per-admin-flag OTP gate: delete only when the required channel
    # verifies. email=require_otp, phone=require_phone_otp, both, or none
    # (both OFF → no code needed). Fail-closed on bypass attempts.
    _raw_e = getattr(org_config, "require_otp", True)
    _raw_p = getattr(org_config, "require_phone_otp", False)
    _need_any_otp = (True if _raw_e is None else bool(_raw_e)) or (
        False if _raw_p is None else bool(_raw_p)
    )
    if _need_any_otp and not flow.get("otp_verified"):
        logger.warning(
            "Cancel OTP gate blocked unverified execution for session %s",
            session_id,
        )
        _otp_channel = _resolve_cancel_otp_channel(
            org_config, channel=str(getattr(chat_session, "channel", "") or "")
        )
        _dest = (
            str(flow.get("phone") or "your phone")
            if _otp_channel == "phone"
            else str(flow.get("email") or "your email address")
        )
        action = ActionPayload(
            message=f"Please enter the verification code sent to **{_dest}** to confirm the cancellation.",
            widget_directive="REQUIRE_OTP_MODAL",
            field_meta={"cancel_flow": True},
        )
        await _save_transcript(
            db, session_id, "cancel my booking", action.message,
            action.widget_directive, intent="CANCEL_BOOKING",
        )
        return action

    booking, start_time = await _fetch_booking(db, org, booking_id)

    if booking is None:
        clear_cancel_flow(chat_session)
        chat_session.pending_cancel = False
        await _persist_session(
            db, chat_session, remove_keys=(CANCEL_FLOW_KEY, *SESSION_RESET_REMOVE_KEYS)
        )
        message = (
            "Good news — that appointment has already been cancelled. "
            "No further action is needed."
        )
        action = ActionPayload(message=message, widget_directive="TEXT_INPUT")
        await _save_transcript(
            db, session_id, "I've verified the code. Please proceed.",
            action.message, "TEXT_INPUT", intent="CANCEL_BOOKING",
        )
        return action

    slot_label = _format_slot_label(start_time, org_config)
    customer_email = booking.customer_email
    customer_name = booking.customer_name
    local_date = ""
    if start_time:
        local_date = start_time.astimezone(_org_timezone(org_config)).strftime("%Y-%m-%d")
    details: Dict[str, Any] = {
        "booking_id": str(booking_id),
        "name": customer_name,
        "email": customer_email,
        "phone": booking.customer_phone or "",
        "date": local_date,
        "time": slot_label,
        "source_channel": "WhatsApp"
        if str(getattr(chat_session, "channel", "") or "").lower()
        in {"whatsapp", "whatsapp_cloud"}
        else "Web Widget",
    }
    if booking.meeting_type_snapshot:
        details["meeting_type_snapshot"] = booking.meeting_type_snapshot
    if booking.meeting_link_status:
        details["meeting_link_status"] = booking.meeting_link_status.value
    if booking.meeting_link_url:
        details["meeting_link_url"] = booking.meeting_link_url
    if booking.meeting_link_provider:
        details["meeting_link_provider"] = booking.meeting_link_provider.value
    if booking.user_timezone:
        details["user_timezone"] = booking.user_timezone
    if booking.org_timezone_snapshot:
        details["org_timezone_snapshot"] = booking.org_timezone_snapshot

    # Best-effort: delete the external calendar event(s) before removing the booking.
    provider = None
    provider_enum = getattr(org_config, "active_calendar_provider", None)
    provider_name = getattr(provider_enum, "value", None)
    if provider_name and str(provider_name).lower() != "none":
        try:
            provider = get_provider(str(provider_name).lower())
        except Exception:
            provider = None
    if provider is not None:
        for attr in ("google_event_id", "outlook_event_id"):
            event_id = getattr(booking, attr)
            if not event_id:
                continue
            try:
                await provider.delete_event(org_config, event_id, db)
            except Exception:
                logger.warning(
                    "Failed to delete external calendar event for booking %s (%s)",
                    booking_id,
                    attr,
                    exc_info=True,
                )

    # Capture original session + customer details BEFORE the delete (row is gone after).
    _orig_session_id = str(getattr(booking, "session_id", "") or "")
    _cancel_phone = booking.customer_phone or ""

    cancelled = await cancel_booking(db, booking_id=booking_id, org_id=org.id)

    # If the booking lived on a different session (book in A, cancel in B via
    # WhatsApp lookup), mark the ORIGINAL session CANCELLED too. Otherwise it
    # stays CONFIRMED with no Booking row — invisible in both bookings lists.
    if _orig_session_id and _orig_session_id != str(session_id):
        try:
            _orig_res = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == uuid.UUID(_orig_session_id)
                )
            )
            _orig_row = _orig_res.scalars().first()
            if _orig_row is not None and _orig_row.org_id == org.id:
                _orig_row.session_status = "CANCELLED"
                try:
                    _orig_data = dict(_orig_row.state_data or {})
                    _orig_data["cancelled_booking_id"] = str(booking_id)
                    _orig_row.state_data = _orig_data
                    from sqlalchemy.orm.attributes import flag_modified as _flag_mod

                    _flag_mod(_orig_row, "state_data")
                except Exception:
                    pass
                await db.commit()
        except Exception:
            logger.warning(
                "Failed to mark original booking session CANCELLED",
                exc_info=True,
            )

    from app.core.org_notify import notify_org

    await notify_org(
        org_id=org.id,
        kind="booking_cancelled",
        title="Appointment cancelled",
        body=(
            f"{customer_name or 'A customer'} cancelled their appointment on "
            f"{details.get('date') or 'an unknown date'} at {details.get('time') or ''}".strip()
            + "."
        ),
        resource_type="booking",
        resource_id=str(booking_id),
    )

    email_ok = True
    try:
        await send_cancellation_email(
            customer_email, customer_name, details, org_id=org.id
        )
    except Exception as mail_err:
        logger.warning("Cancellation email failed: %s", mail_err)
        email_ok = False

    old_values = {"status": "CONFIRMED", "date": details.get("date"), "time": details.get("time")}
    new_values = {"status": "CANCELLED", "date": details.get("date"), "time": details.get("time")}
    if background_tasks is not None:
        log_audit_event(
            background_tasks,
            org_id=org.id,
            actor_email=customer_email,
            action=AuditAction.BOOKING_CANCEL,
            resource_type="booking",
            resource_id=booking_id,
            old_values=old_values,
            new_values=new_values,
        )
    else:
        await log_audit_event_async(
            org_id=org.id,
            actor_email=customer_email,
            action=AuditAction.BOOKING_CANCEL,
            resource_type="booking",
            resource_id=booking_id,
            old_values=old_values,
            new_values=new_values,
        )

    # Record the cancellation on the session (CANCELLED is the authoritative
    # terminal status consumed by the dashboard, cancelled list and pie chart).
    # The user can rebook from here — the CANCELLED/EXPIRED rebook path resets
    # the session to ACTIVE when a new booking is requested.
    keep_keys = {
        crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY,
        "date",
        "time",
    }
    collected = dict(chat_session.collected_data or {})
    pruned = {
        key: value for key, value in collected.items() if key in keep_keys
    }
    # Stamp cancelled booking details onto the cancel session so
    # GET /dashboard/bookings/cancelled (reads state_data name/email/date)
    # shows a recognizable row even when cancel happens from a different
    # session than the original booking (WhatsApp lookup flow).
    if customer_name and not pruned.get("name"):
        pruned["name"] = customer_name
    if customer_email and not pruned.get("email"):
        pruned["email"] = customer_email
    if _cancel_phone and not pruned.get("phone"):
        pruned["phone"] = _cancel_phone
    if local_date and not pruned.get("date"):
        pruned["date"] = local_date
    # Keep the deleted booking's id so the frontend can highlight this row
    # when arriving from a booking_cancelled notification (?highlight=booking_id).
    pruned["cancelled_booking_id"] = str(booking_id)
    chat_session.collected_data = {
        k: v for k, v in pruned.items() if v is not None
    }
    chat_session.missing_fields = []
    chat_session.current_intent = "START_BOOKING"
    chat_session.session_status = "CANCELLED"
    chat_session.otp_verified = False
    chat_session.phone_otp_verified = False
    chat_session.pending_cancel = False
    _cancel_remove_keys = tuple(
        k for k in SESSION_RESET_REMOVE_KEYS if k not in ("name", "email", "phone")
    )
    await _persist_session(
        db,
        chat_session,
        remove_keys=(
            CANCEL_FLOW_KEY,
            "_active_path_effects",
            "_otp_policy",
            "_internal",
            *_cancel_remove_keys,
        ),
    )

    footer = (
        "A cancellation email is on its way."
        if email_ok
        else "I couldn't send the cancellation email right now, but your appointment is cancelled."
    )
    message = (
        f"**{slot_label}** has been cancelled, and the slot is now free. {footer}"
    )
    action = ActionPayload(
        message=message,
        widget_directive=CANCELLED_DIRECTIVE,
        field_meta={"booking": details},
        is_terminal=True,
    )
    await _save_transcript(
        db,
        session_id,
        user_message or "[verification code entered]",
        action.message,
        CANCELLED_DIRECTIVE,
        intent="CANCEL_BOOKING",
    )
    logger.info(
        "Booking cancelled via widget flow",
        extra={"session_id": session_id, "booking_id": booking_id},
    )
    return action
