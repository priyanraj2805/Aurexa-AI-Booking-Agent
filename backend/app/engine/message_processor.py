"""
Channel-agnostic message processing engine.

This is the HEART of the booking flow — extracted from chat.py so that
both the web widget and WhatsApp (and any future channel) use the exact
same deterministic pipeline.

The LLM proposes intent + entities; Python validates, overrides, and
decides the next action. No business logic lives in the LLM.
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from asgi_correlation_id.context import correlation_id

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

from app.models.organization import Organization
from app.models.config import OrgConfig, MeetingMode, MeetingProviderDefault
from app.models.booking import TimeSlot, Booking
from app.schemas.agent import (
    ActionPayload,
    ChatSessionState,
    FieldDefinition,
    parse_field_definitions,
    get_required_field_names,
)
from app.crud import chat as crud_chat
from app.engine.orchestrator import LLMOrchestrator
from app.engine.evaluator import StateEvaluator
from app.engine.recipient_resolution import resolve_email_from_collected_data
from app.engine.validators import DataValidator
from app.core.audit import log_audit_event
from app.schemas.audit import AuditAction
from app.crud.monitoring import create_system_alert
from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate
from app.engine.scheduling_policy import (
    resolve_effective_timezone_policy,
    evaluate_same_day_policy,
    evaluate_slot_cutoff_policy,
)

logger = logging.getLogger(__name__)
llm_engine = LLMOrchestrator()


def _normalize_meeting_type(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    value = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    if value in {"ONLINE", "OFFLINE"}:
        return value
    return None


def _resolve_effective_user_timezone(
    raw_user_timezone: Optional[str], org_timezone: str
) -> tuple[str, bool, Optional[str]]:
    org_normalized = org_timezone
    try:
        org_normalized = ZoneInfo(org_timezone).key
    except Exception:
        org_normalized = "UTC"

    if not raw_user_timezone:
        return org_normalized, True, "missing_tz"
    try:
        requested_zone = ZoneInfo(raw_user_timezone)
        return requested_zone.key, False, None
    except Exception:
        return org_normalized, True, "invalid_tz"


def _resolve_selected_slot_start_utc(
    selected_slot_id: Optional[str], org_id: str
) -> Optional[datetime]:
    if not selected_slot_id:
        return None
    raw = str(selected_slot_id).strip()
    if raw.startswith("virtual|"):
        parts = raw.split("|", 2)
        if len(parts) != 3:
            return None
        virtual_org_id = parts[1]
        if virtual_org_id != str(org_id):
            return None
        try:
            start_utc = datetime.fromisoformat(parts[2])
        except Exception:
            return None
        if start_utc.tzinfo is None:
            return start_utc.replace(tzinfo=timezone.utc)
        return start_utc.astimezone(timezone.utc)
    return None


def _resolve_lock_slot_start_utc(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clear_lock_slot_metadata(chat_session: ChatSessionState) -> None:
    chat_session.collected_data.pop("locked_slot_id", None)
    chat_session.collected_data.pop("locked_slot_start_utc", None)


def _normalize_meeting_mode(mode: MeetingMode | str | None) -> MeetingMode:
    if isinstance(mode, MeetingMode):
        return mode
    if isinstance(mode, str):
        try:
            return MeetingMode(mode)
        except ValueError:
            return MeetingMode.OFFLINE_ONLY
    return MeetingMode.OFFLINE_ONLY


def _normalize_meeting_provider(
    provider: MeetingProviderDefault | str | None,
) -> MeetingProviderDefault:
    if isinstance(provider, MeetingProviderDefault):
        return provider
    if isinstance(provider, str):
        try:
            return MeetingProviderDefault(provider)
        except ValueError:
            return MeetingProviderDefault.NONE
    return MeetingProviderDefault.NONE


def _allowed_meeting_types(
    mode: MeetingMode | str, allow_hybrid_selection: bool
) -> list[str]:
    normalized_mode = _normalize_meeting_mode(mode)
    if normalized_mode == MeetingMode.ONLINE_ONLY:
        return ["ONLINE"]
    if normalized_mode == MeetingMode.OFFLINE_ONLY:
        return ["OFFLINE"]
    if allow_hybrid_selection:
        return ["ONLINE", "OFFLINE"]
    return ["OFFLINE"]


CONFIRM_KEYWORDS = [
    "yes",
    "confirm",
    "go ahead",
    "book it",
    "proceed",
    "complete",
    "do it",
    "make it",
    "sure",
    "finalize",
    "lock it",
    "schedule it",
    "ok",
    "okay",
    "yep",
    "yup",
    "alright",
    "absolutely",
    "please do",
    "that's it",
    "thats it",
    "just do it",
    "get it done",
    "approved",
    "done",
    "confirmed",
    "proceed with booking",
]

RESEND_KEYWORDS = [
    "resend",
    "re-send",
    "send again",
    "new code",
    "didn't get",
    "didnt get",
]

EDIT_DATA_KEYWORDS = [
    "change details",
    "change some details",
    "change something",
    "edit details",
    "update details",
    "modify details",
    "i need to change",
    "i want to change",
]


def _is_edit_details_request(message_lower: str) -> bool:
    return any(keyword in (message_lower or "") for keyword in EDIT_DATA_KEYWORDS)


def _ensure_internal_state(chat_session: ChatSessionState) -> Dict[str, Any]:
    if not isinstance(chat_session.collected_data, dict):
        chat_session.collected_data = {}
    internal = chat_session.collected_data.get("_internal")
    if not isinstance(internal, dict):
        internal = {}
        chat_session.collected_data["_internal"] = internal
    return internal


def _resolve_session_email(
    chat_session: ChatSessionState,
    fd_map: Optional[Dict[str, FieldDefinition]] = None,
) -> str:
    definitions = list(fd_map.values()) if isinstance(fd_map, dict) else None
    return resolve_email_from_collected_data(chat_session.collected_data, definitions)


def _build_otp_context_key(
    chat_session: ChatSessionState,
    fd_map: Optional[Dict[str, FieldDefinition]] = None,
) -> str:
    if not isinstance(chat_session.collected_data, dict):
        return f"otp:{chat_session.session_id}:::"
    email = _resolve_session_email(chat_session, fd_map)
    date_value = _normalize_otp_date_component(chat_session.collected_data.get("date"))
    time_value = _normalize_otp_time_component(chat_session.collected_data.get("time"))
    return f"otp:{chat_session.session_id}:{email}:{date_value}:{time_value}"


def _normalize_otp_date_component(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return text


def _normalize_otp_time_component(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%H:%M")
    except Exception:
        return text

LLM_TEMPORARY_FALLBACK_FRAGMENTS = (
    "trouble connecting to my brain",
    "finding that a bit hard to process",
)


def _is_llm_temporary_fallback(
    response_message: str, extracted_entities: Dict[str, Any]
) -> bool:
    if extracted_entities:
        return False
    normalized = (response_message or "").lower()
    return any(fragment in normalized for fragment in LLM_TEMPORARY_FALLBACK_FRAGMENTS)


def _should_attempt_fallback_extraction(
    response_message: str,
    extracted_entities: Dict[str, Any],
    missing_fields: list[str],
) -> bool:
    """
    Determine if we should attempt Python-side fallback extraction.
    Triggers when:
    - LLM returned empty entities AND there are missing fields
    - LLM response contains a fallback phrase
    This is broader than _is_llm_temporary_fallback — it catches cases
    where the LLM simply didn't extract anything (not just when it says so).
    """
    if extracted_entities:
        return False
    if not missing_fields:
        return False
    normalized = (response_message or "").lower()
    if any(fragment in normalized for fragment in LLM_TEMPORARY_FALLBACK_FRAGMENTS):
        return True
    generic_responses = {
        "i see. please tell me more.",
        "i see.",
        "please tell me more.",
        "could you repeat that please?",
        "i'm sorry, i'm finding that a bit hard to process. could you rephrase it for me?",
    }
    if normalized.strip() in generic_responses:
        return True
    if len(normalized) < 10:
        return True
    return False


def _looks_like_time_candidate(value: str) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return False
    if raw in {"noon", "midnight"}:
        return True
    if re.search(r"\b(am|pm)\b", raw):
        return True
    if re.search(r"\b\d{1,2}:\d{2}\b", raw):
        return True
    return False


def _is_date_only_value(value: str) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return False
    return bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw)
        or re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", raw)
    )


def _looks_like_date_candidate(value: str) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return False
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", raw):
        return True
    if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", raw):
        return True
    if re.search(
        r"\b(today|tomorrow|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
        raw,
    ):
        return True
    return False


def _looks_like_name_candidate(value: str) -> bool:
    raw = " ".join((value or "").split())
    if not raw:
        return False
    if raw.lower() in {
        "hi",
        "hii",
        "hiii",
        "hiiii",
        "hello",
        "hey",
        "yo",
        "sup",
        "ok",
        "okay",
        "sure",
        "yes",
        "no",
        "thanks",
        "thank you",
    }:
        return False
    if any(c.isdigit() for c in raw):
        return False
    alpha_chars = sum(1 for c in raw if c.isalpha())
    return alpha_chars >= 2


def _extract_entities_without_llm(
    message_text: str,
    missing_fields: list[str],
    field_definitions: list[FieldDefinition],
    timezone_name: str,
    max_booking_days: int,
) -> Dict[str, Any]:
    extracted: Dict[str, Any] = {}
    fd_map = {fd.name: fd for fd in field_definitions}

    ordered_missing = list(missing_fields or [])
    if "time" in ordered_missing:
        ordered_missing = ["time"] + [f for f in ordered_missing if f != "time"]

    for field_name in ordered_missing:
        fd = fd_map.get(field_name)
        field_type = fd.type if fd else "text"

        parseable_structured = field_type in {
            "email",
            "phone",
            "date",
            "time",
            "select",
            "number",
        }
        parseable_name = field_name == "name"
        parseable_custom_text = field_type in {"text", "textarea"}

        if (
            not parseable_structured
            and not parseable_name
            and not parseable_custom_text
        ):
            continue

        if parseable_name:
            if not _looks_like_name_candidate(message_text):
                continue
            parsed_name, err = DataValidator._validate_name(message_text)
            if not err and parsed_name:
                extracted[field_name] = parsed_name
            continue

        if parseable_custom_text:
            stripped = message_text.strip()
            if len(stripped) < 1 or len(stripped) > 255:
                continue
            if stripped.lower() in {
                "hi",
                "hello",
                "hey",
                "hii",
                "hiii",
                "yo",
                "sup",
                "ok",
                "okay",
                "yes",
                "no",
                "sure",
                "thanks",
                "thank you",
            }:
                continue
            valid, _ = DataValidator.validate_entities(
                {field_name: stripped},
                field_definitions,
                timezone=timezone_name,
                max_booking_days=max_booking_days,
            )
            if field_name in valid:
                extracted[field_name] = valid[field_name]
            continue

        if field_type == "time" and not _looks_like_time_candidate(message_text):
            continue
        if field_type == "date" and not _looks_like_date_candidate(message_text):
            continue
        if field_type == "email" and "@" not in message_text:
            continue
        if field_type == "phone" and len(re.sub(r"\D", "", message_text)) < 7:
            continue

        valid, _ = DataValidator.validate_entities(
            {field_name: message_text},
            field_definitions,
            timezone=timezone_name,
            max_booking_days=max_booking_days,
        )
        if field_name in valid:
            extracted[field_name] = valid[field_name]

    return extracted


def _label_to_request(label: str, verb: str = "share") -> str:
    """
    Convert a field label into a natural request phrase.
    Handles:
    - Question-style labels ("Where did you hear about us?")
    - Labels containing "your" ("Alternate Mails of your colleagues")
    - Normal labels ("Organisation Name", "Email Address")
    """
    raw = (label or "").strip()
    if not raw:
        return "the required information"

    lower = raw.lower()

    # Question-style labels — rephrase as a request
    if lower.endswith("?"):
        question_text = raw.rstrip("?").strip()
        return f"your answer to: {question_text}"

    # Labels already containing "your" — don't add another
    if "your " in lower or "your's" in lower:
        return raw

    # Standard labels — use the verb
    return f"your {lower}"


def _build_llm_recovery_message(
    directive: str,
    field_meta: Optional[Dict[str, Any]],
) -> str:
    lead = (
        "I'm having a temporary issue understanding full messages, but we can continue."
    )

    if directive == "REQUIRE_TIME_SLOT_GRID":
        return (
            f"{lead} Please choose your preferred time from the available slots below."
        )
    if directive == "REQUIRE_DATE_PICKER":
        return f"{lead} Please choose your preferred date."
    if directive == "REQUIRE_EMAIL_INPUT":
        return f"{lead} Please share your email address."
    if directive == "REQUIRE_PHONE_INPUT":
        return f"{lead} Please share your phone number."
    if directive == "REQUIRE_SELECT":
        label = (field_meta or {}).get("label") or "an option"
        return f"{lead} Please choose {label} from the options shown."
    if directive == "REQUIRE_TEXTAREA":
        return f"{lead} Please provide the requested details in the text box."
    if directive == "REQUIRE_NUMBER_INPUT":
        return f"{lead} Please provide the required number."
    if directive == "TEXT_INPUT":
        label = (field_meta or {}).get("label")
        if label:
            return f"{lead} Please share your {str(label).strip().lower()}."
        return f"{lead} Please share the required detail."
    return f"{lead} Please repeat your last answer once more."


def _build_context_aware_response(
    directive: str,
    field_meta: Optional[Dict[str, Any]],
    collected_data: Dict[str, Any],
    valid_data: Dict[str, Any],
    errors: Dict[str, str],
    llm_generated_msg: str,
    agent_name: str = "Booking Assistant",
) -> str:
    """
    Generate a deterministic, context-aware response that stays in sync
    with the widget directive. Overrides the LLM response when:
    - The LLM asks for a field that isn't the next missing one
    - The LLM doesn't acknowledge newly provided data
    - The LLM response contradicts the directive
    """
    booking_field_meta: Optional[Dict[str, Any]] = None
    if errors:
        return llm_generated_msg

    field_label = (field_meta or {}).get("label", "")
    field_name = (field_meta or {}).get("name", "")

    user_name = collected_data.get("name", "")

    if directive == "REQUIRE_TIME_SLOT_GRID":
        date_val = collected_data.get("date", "")
        display_timezone = str(
            collected_data.get("user_timezone")
            or collected_data.get("org_timezone_snapshot")
            or ""
        ).strip()
        fallback_used = bool(collected_data.get("timezone_fallback_used"))
        if fallback_used:
            timezone_note = " We couldn't detect your timezone, so these times are shown in the business timezone."
        elif display_timezone:
            timezone_note = f" Times are shown in {display_timezone}."
        else:
            timezone_note = ""

        if date_val:
            try:
                from dateutil.parser import parse as date_parse

                parsed = date_parse(date_val)
                date_str = parsed.strftime("%B %d, %Y")
            except Exception:
                date_str = date_val
            if user_name:
                return f"Thank you, {user_name.split()[0]}. Now, please choose your preferred time slot for {date_str} from the options below.{timezone_note}"
            return f"Thank you for providing the date. Now, please choose your preferred time slot for {date_str} from the options below.{timezone_note}"
        return f"Please choose your preferred time from the available slots below.{timezone_note}"

    if directive == "REQUIRE_DATE_PICKER":
        if user_name:
            return f"Thank you, {user_name.split()[0]}. Please choose your preferred date for the appointment."
        return "Please choose your preferred date for the appointment."

    if directive == "REQUIRE_EMAIL_INPUT":
        if user_name:
            return f"Thank you, {user_name.split()[0]}. Please share your email address so I can send you a verification code."
        return "Please share your email address so I can send you a verification code."

    if directive == "REQUIRE_PHONE_INPUT":
        if user_name:
            return f"Thank you, {user_name.split()[0]}. Please share your phone number so we can reach you if needed."
        return "Please share your phone number so we can reach you if needed."

    if directive == "REQUIRE_SELECT":
        if field_label:
            request_phrase = _label_to_request(field_label, "select")
            if user_name:
                return f"Thank you, {user_name.split()[0]}. Please select {request_phrase} from the options shown below."
            return f"Please select {request_phrase} from the options shown below."
        return "Please choose from the options shown below."

    if directive == "REQUIRE_TEXTAREA":
        if field_label:
            request_phrase = _label_to_request(field_label, "provide")
            if user_name:
                return f"Thank you, {user_name.split()[0]}. Please provide {request_phrase}."
            return f"Please provide {request_phrase}."
        return "Please provide the requested details."

    if directive == "REQUIRE_NUMBER_INPUT":
        if field_label:
            request_phrase = _label_to_request(field_label, "enter")
            if user_name:
                return (
                    f"Thank you, {user_name.split()[0]}. Please enter {request_phrase}."
                )
            return f"Please enter {request_phrase}."
        return "Please enter the required number."

    if directive == "TEXT_INPUT":
        if field_label:
            request_phrase = _label_to_request(field_label, "share")
            if user_name:
                return (
                    f"Thank you, {user_name.split()[0]}. Please share {request_phrase}."
                )
            return f"Please share {request_phrase}."
        if field_name:
            request_phrase = _label_to_request(
                field_name.replace("_", " ").title(), "share"
            )
            if user_name:
                return (
                    f"Thank you, {user_name.split()[0]}. Please share {request_phrase}."
                )
            return f"Please share {request_phrase}."
        return "Please provide the required information."

    if directive == "REQUEST_CONFIRMATION":
        return llm_generated_msg

    if directive == "REQUIRE_OTP_MODAL":
        return llm_generated_msg

    if directive == "BOOKING_CONFIRMED_CARD":
        return llm_generated_msg

    if directive == "NONE":
        return llm_generated_msg

    return llm_generated_msg


async def process_message(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """
    Process a single user message through the booking pipeline.
    Returns an ActionPayload with the agent's response + directive.

    This function is CHANNEL-AGNOSTIC — it doesn't know or care whether
    the message came from the web widget, WhatsApp, or SMS.
    """

    # Recover deterministic stuck state: OTP verified but still marked CONFIRMING.
    if (
        chat_session.session_status == "CONFIRMING"
        and chat_session.otp_verified
        and not chat_session.missing_fields
    ):
        chat_session.session_status = "ACTIVE"
        chat_session.current_intent = "CONFIRM_BOOKING"
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    # ── Post-Booking States (CONFIRMED / CANCELLED / EXPIRED) ──
    # Instead of a hard wall, handle post-booking requests conversationally.
    # CONFIRMING is the only truly locked state (booking in progress).
    if chat_session.session_status == "CONFIRMING":
        msg = "Your booking is being processed right now. Please wait a moment..."
        action = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", is_terminal=False
        )
        await _save_transcript(
            db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
        )
        return action

    if chat_session.session_status in ("CONFIRMED", "CANCELLED", "EXPIRED"):
        return await _handle_post_booking(
            db, org, org_config, chat_session, message_text, background_tasks
        )

    # ── Load config values ────────────────────────────────────
    persona = (
        org_config.custom_prompts if isinstance(org_config.custom_prompts, dict) else {}
    )
    org_timezone = str(org_config.timezone) if org_config.timezone else "UTC"
    require_otp = getattr(org_config, "require_otp", True)
    max_booking_days = getattr(org_config, "max_booking_days_ahead", 90)

    field_definitions = parse_field_definitions(
        org_config.field_definitions or org_config.required_fields
    )
    fd_map = {fd.name: fd for fd in field_definitions}

    # ── LLM Orchestration (Intent + Extraction + Response) ────
    prior_intent = chat_session.current_intent
    previous_missing_fields = list(chat_session.missing_fields or [])
    unified_result = await llm_engine.process_turn(
        message_text,
        chat_session.missing_fields,
        chat_session.collected_data,
        chat_session.current_intent,
        persona=persona,
        field_definitions=field_definitions,
        org_id=str(org.id) if org else None,
        db=db,
    )

    intent = unified_result.get("intent", chat_session.current_intent or "PROVIDE_DATA")
    chat_session.current_intent = intent
    raw_entities = unified_result.get("extracted_entities", {})
    llm_generated_msg = unified_result.get(
        "response_message", "I see. Please tell me more."
    )
    llm_temporary_fallback = _is_llm_temporary_fallback(llm_generated_msg, raw_entities)

    should_fallback = llm_temporary_fallback or _should_attempt_fallback_extraction(
        llm_generated_msg, raw_entities, chat_session.missing_fields or []
    )
    if should_fallback:
        fallback_entities = _extract_entities_without_llm(
            message_text,
            chat_session.missing_fields or [],
            field_definitions,
            timezone_name=org_timezone,
            max_booking_days=max_booking_days,
        )
        if fallback_entities:
            raw_entities = {**raw_entities, **fallback_entities}
            if not llm_temporary_fallback:
                llm_temporary_fallback = True

    user_msg_lower = message_text.lower().strip()
    force_confirm_intent = (
        prior_intent == "CONFIRM_BOOKING"
        and not chat_session.missing_fields
        and any(kw in user_msg_lower for kw in CONFIRM_KEYWORDS)
    )
    if force_confirm_intent:
        intent = "CONFIRM_BOOKING"
        chat_session.current_intent = intent

    # ── CANCEL override ───────────────────────────────────────
    # Do not allow LLM misclassification to bypass explicit confirmation
    # when the session is already at the commit-ready confirmation step.
    if intent == "CANCEL_BOOKING":
        chat_session.session_status = "CANCELLED"
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        action = ActionPayload(
            message="No problem at all! Your booking request has been cancelled. If you change your mind, just let me know — I'm happy to help you rebook anytime! 😊",
            widget_directive="TEXT_INPUT",
            is_terminal=False,
        )
        await _save_transcript(
            db,
            chat_session.session_id,
            message_text,
            action.message,
            "TEXT_INPUT",
            intent="CANCEL_BOOKING",
        )
        return action

    # ── CONFIRM override (deterministic Python-side) ──────────
    if not chat_session.missing_fields and any(
        kw in user_msg_lower for kw in CONFIRM_KEYWORDS
    ):
        intent = "CONFIRM_BOOKING"
        chat_session.current_intent = intent

    # ── Edit-details override (deterministic Python-side) ─────
    if not chat_session.missing_fields and _is_edit_details_request(user_msg_lower):
        intent = "EDIT_DATA"
        chat_session.current_intent = intent

    # ── Resend OTP override ───────────────────────────────────
    if (
        require_otp
        and not chat_session.otp_verified
        and any(kw in user_msg_lower for kw in RESEND_KEYWORDS)
    ):
        return await _handle_otp_resend(
            db, org, chat_session, message_text, background_tasks
        )

    # ── Change date override during time selection ────────────
    CHANGE_DATE_KEYWORDS = [
        "change the date",
        "change date",
        "different date",
        "another date",
        "wrong date",
        "pick another date",
        "choose another date",
        "change my date",
        "switch date",
        "update date",
        "new date",
        "not available on",
        "no slots",
        "no availability",
    ]
    if (
        "time" not in (chat_session.missing_fields or [])
        and "date" not in (chat_session.missing_fields or [])
        and any(kw in user_msg_lower for kw in CHANGE_DATE_KEYWORDS)
    ):
        chat_session.missing_fields = ["date"] + [
            f for f in (chat_session.missing_fields or []) if f != "date"
        ]
        chat_session.collected_data.pop("time", None)
        chat_session.collected_data.pop("selected_slot_id", None)
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        msg = _build_context_aware_response(
            directive="REQUIRE_DATE_PICKER",
            field_meta=None,
            collected_data=chat_session.collected_data,
            valid_data={},
            errors={},
            llm_generated_msg=llm_generated_msg,
            agent_name=persona.get("agent_name", "Booking Assistant"),
        )
        action = ActionPayload(message=msg, widget_directive="REQUIRE_DATE_PICKER")
        await _save_transcript(
            db, chat_session.session_id, message_text, msg, "REQUIRE_DATE_PICKER"
        )
        return action

    # ── Change collected field override (deterministic Python-side) ──
    # When the user asks to change a field that was already collected, re-ask
    # exactly that field instead of emitting the next missing field's widget.
    # Skips if the same turn already contains the new value (normal merge path).
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
        "time": [
            "change time",
            "change my time",
            "change the time",
            "wrong time",
            "update time",
            "different time",
        ],
    }
    missing_set = set(chat_session.missing_fields or [])
    changed_field = None
    for field, kws in CHANGE_COLLECTED_FIELD_KEYWORDS.items():
        if field in missing_set:
            continue
        if field not in chat_session.collected_data:
            continue
        if (raw_entities or {}).get(field):
            continue
        if any(kw in user_msg_lower for kw in kws):
            changed_field = field
            break
    if changed_field:
        chat_session.collected_data.pop(changed_field, None)
        if changed_field == "time":
            chat_session.collected_data.pop("selected_slot_id", None)
            _clear_lock_slot_metadata(chat_session)
        chat_session.missing_fields = [changed_field] + [
            f for f in (chat_session.missing_fields or []) if f != changed_field
        ]
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        next_step = StateEvaluator.determine_next_action(
            chat_session.missing_fields, chat_session.current_intent, field_definitions
        )
        changed_directive = str(next_step.get("directive") or "TEXT_INPUT")
        msg = _build_context_aware_response(
            directive=changed_directive,
            field_meta=next_step.get("field_meta"),
            collected_data=chat_session.collected_data,
            valid_data={},
            errors={},
            llm_generated_msg=llm_generated_msg,
            agent_name=persona.get("agent_name", "Booking Assistant"),
        )
        action = ActionPayload(
            message=msg,
            widget_directive=changed_directive,
            field_meta=next_step.get("field_meta"),
        )
        await _save_transcript(
            db, chat_session.session_id, message_text, msg, changed_directive
        )
        return action

    # ── OTP Verification (for channels without modal support like WhatsApp) ─
    match = None
    if (
        require_otp
        and not chat_session.otp_verified
        and chat_session.session_status == "ACTIVE"
        and (not chat_session.missing_fields or len(chat_session.missing_fields) == 0)
    ):
        import re

        otp_pattern = re.compile(r"\b(\d{6})\b")
        match = otp_pattern.search(message_text)

        if match:
            otp_code = match.group(1)
            from app.crud.otp import verify_otp as crud_verify_otp

            is_valid = await crud_verify_otp(db, chat_session.session_id, otp_code)
            if is_valid:
                chat_session.otp_verified = True
                chat_session.current_intent = "CONFIRM_BOOKING"
                await crud_chat.update_chat_session(
                    db, chat_session.session_id, chat_session
                )
                logger.info(f"OTP verified for session {chat_session.session_id}")

                msg, directive, booking_field_meta = await _handle_booking_flow(
                    db,
                    org,
                    org_config,
                    chat_session,
                    "CONFIRM_BOOKING",
                    require_otp,
                    fd_map,
                    background_tasks,
                )
                action = ActionPayload(
                    message=msg,
                    widget_directive=directive,
                    field_meta=booking_field_meta,
                )
                await _save_transcript(
                    db,
                    chat_session.session_id,
                    message_text,
                    msg,
                    directive,
                    intent="CONFIRM_BOOKING",
                )
                return action
            else:
                from app.crud.otp import get_otp_policy_metadata

                action = ActionPayload(
                    message="Invalid or expired OTP code. Please try again or say 'resend' to get a new code.",
                    widget_directive="REQUIRE_OTP_MODAL",
                    field_meta=await get_otp_policy_metadata(db, chat_session.session_id),
                )
                await _save_transcript(
                    db,
                    chat_session.session_id,
                    message_text,
                    action.message,
                    "REQUIRE_OTP_MODAL",
                )
                return action

    # ── Data Validation ───────────────────────────────────────
    # Filter out LLM hallucinations: only keep entities for the primary
    # missing field (or fields the user could reasonably have answered).
    # This prevents the LLM from filling all 9 missing fields with "hiii".
    primary_missing = (
        chat_session.missing_fields[0] if chat_session.missing_fields else None
    )
    if raw_entities and primary_missing:
        filtered_entities = {}
        for field, value in raw_entities.items():
            if field == primary_missing:
                filtered_entities[field] = value
            elif field in chat_session.collected_data:
                # User is updating an already-collected field — allow it
                filtered_entities[field] = value
            elif field in {"name", "email", "phone"}:
                # Core identity fields can be provided at any time
                filtered_entities[field] = value
        raw_entities = filtered_entities

    valid_data, errors = DataValidator.validate_entities(
        raw_entities,
        field_definitions,
        timezone=org_timezone,
        max_booking_days=max_booking_days,
    )
    logger.info(f"Valid fields: {list(valid_data.keys())}")
    if errors:
        logger.warning(f"Validation Errors: {list(errors.keys())}")

    # Update session state with validated data
    chat_session.collected_data.update(valid_data)

    # Recalculate missing fields
    tenant_required_fields = get_required_field_names(field_definitions)
    chat_session.missing_fields = StateEvaluator.calculate_missing_fields(
        tenant_required_fields, chat_session.collected_data
    )

    # Persist
    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    logger.info("--- SESSION STATE UPDATED ---")
    logger.info(f"Intent: {chat_session.current_intent}")
    logger.info(f"Collected fields: {list(chat_session.collected_data.keys())}")
    logger.info(f"Missing: {chat_session.missing_fields}")
    logger.info("-----------------------------")

    # ── Determine next action ─────────────────────────────────
    next_step = StateEvaluator.determine_next_action(
        chat_session.missing_fields, chat_session.current_intent, field_definitions
    )

    manual_time_requires_slot_selection = (
        str(getattr(chat_session, "channel", "")).lower() == "web"
        and "time" in previous_missing_fields
        and "time" not in (chat_session.missing_fields or [])
        and not str(chat_session.collected_data.get("selected_slot_id") or "").strip()
    )
    if manual_time_requires_slot_selection:
        chat_session.collected_data.pop("time", None)
        _clear_lock_slot_metadata(chat_session)
        chat_session.missing_fields = [
            "time",
            *[field for field in (chat_session.missing_fields or []) if field != "time"],
        ]
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        next_step = StateEvaluator.determine_next_action(
            chat_session.missing_fields, chat_session.current_intent, field_definitions
        )

    if errors:
        # Only show validation errors when the user actually tried to provide
        # data for the primary missing field but it was invalid (bad format,
        # out of range, etc.). If the user didn't provide any value at all
        # (just asked a question, complained, etc.), let the LLM's conversational
        # response through — don't error out on natural conversation.
        primary_missing = (
            chat_session.missing_fields[0] if chat_session.missing_fields else None
        )
        user_tried_to_provide = primary_missing and primary_missing in raw_entities

        if user_tried_to_provide and primary_missing in errors:
            error_msg = errors[primary_missing]
            msg = f"I couldn't quite understand that:\n{error_msg}\nPlease try again."
            directive = next_step.get("directive", "TEXT_INPUT")
        else:
            # User didn't try to provide data — conversational turn.
            # Let the LLM handle it naturally (answer questions, explain why
            # fields are needed, handle complaints).
            directive = next_step.get("directive", "TEXT_INPUT")
            msg = llm_generated_msg
    elif next_step["directive"] == "PROCESS_BOOKING":
        msg, directive, booking_field_meta = await _handle_booking_flow(
            db,
            org,
            org_config,
            chat_session,
            intent,
            require_otp,
            fd_map,
            background_tasks,
        )
    else:
        directive = next_step["directive"]

        if manual_time_requires_slot_selection:
            directive = "REQUIRE_TIME_SLOT_GRID"
            msg = "Please select your preferred time from the available slot buttons below."
        elif llm_temporary_fallback:
            msg = _build_llm_recovery_message(directive, next_step.get("field_meta"))
        elif valid_data:
            # User provided valid data — use deterministic response to ask
            # for the next field in order. This keeps the flow stable.
            msg = _build_context_aware_response(
                directive=directive,
                field_meta=next_step.get("field_meta"),
                collected_data=chat_session.collected_data,
                valid_data=valid_data,
                errors=errors,
                llm_generated_msg=llm_generated_msg,
                agent_name=persona.get("agent_name", "Booking Assistant"),
            )
        else:
            # User didn't provide data (question, complaint, greeting, etc.)
            # Let the LLM handle the conversation naturally.
            msg = llm_generated_msg

        # Re-show summary after EDIT_DATA only after an actual field change.
        if intent == "EDIT_DATA" and valid_data and not chat_session.missing_fields:
            msg, directive = _build_edit_summary(chat_session, valid_data, fd_map)
        elif intent == "EDIT_DATA" and not valid_data:
            directive = "TEXT_INPUT"
            msg = "Sure - tell me what you'd like to change (name, email, phone, date, or time)."
        elif directive == "REQUEST_CONFIRMATION":
            msg, directive = _build_confirmation_summary(chat_session, fd_map)

    field_meta = booking_field_meta if booking_field_meta is not None else next_step.get("field_meta")
    has_collected_data = bool(chat_session.collected_data)
    collected_keys = (
        list(chat_session.collected_data.keys()) if chat_session.collected_data else []
    )
    logger.info(
        f"DEBUG: directive={directive}, field_meta has options: {field_meta.get('options') if field_meta else None}, has_collected_data={has_collected_data}, collected_keys={collected_keys}"
    )

    # Fetch available time slots when directive is REQUIRE_TIME_SLOT_GRID
    if directive == "REQUIRE_TIME_SLOT_GRID" and field_meta:
        target_date = chat_session.collected_data.get("date")
        logger.info(f"DEBUG: target_date={target_date}, org.id={org.id}")
        if target_date:
            from dateutil.parser import parse as date_parse
            from app.engine.slot_engine import (
                compute_slot_inventory,
                format_slot_time,
                apply_scheduling_policy_to_inventory,
            )

            try:
                parsed_date = date_parse(target_date).date()
                inventory, availability_status = await compute_slot_inventory(
                    db, org.id, parsed_date, org_config=org_config
                )
                timezone_resolution = resolve_effective_timezone_policy(
                    requested_tz=chat_session.collected_data.get("user_timezone"),
                    org_tz=org_timezone,
                    requested_source=chat_session.collected_data.get("timezone_source"),
                )
                inventory, availability_status, policy_reason_code = (
                    apply_scheduling_policy_to_inventory(
                        inventory,
                        parsed_date,
                        base_status=availability_status,
                        effective_timezone=str(timezone_resolution["effective_timezone"]),
                        allow_same_day_booking=bool(
                            getattr(org_config, "allow_same_day_booking", False)
                        ),
                        slot_cutoff_minutes=getattr(
                            org_config, "slot_cutoff_minutes", 5
                        ),
                    )
                )
                options = [
                    format_slot_time(item["start_utc"].time())
                    for item in inventory
                    if item["available"]
                ]
                field_meta["options"] = options
                field_meta["availability_status"] = availability_status
                if policy_reason_code:
                    field_meta["policy_reason_code"] = policy_reason_code
                logger.info(
                    f"Computed {len(options)} available slots for {target_date}: {options}"
                )

                # If no slots available, revert to date picker instead of
                # asking for a time that doesn't exist.
                if availability_status == "same_day_disabled":
                    directive = "REQUIRE_DATE_PICKER"
                    msg = (
                        "Same-day booking isn't available for this business. "
                        "Please choose a future date."
                    )
                    chat_session.collected_data.pop("date", None)
                    chat_session.collected_data.pop("time", None)
                    chat_session.collected_data.pop("selected_slot_id", None)
                    if "date" not in chat_session.missing_fields:
                        chat_session.missing_fields = ["date"] + list(
                            chat_session.missing_fields or []
                        )
                elif not options:
                    directive = "REQUIRE_DATE_PICKER"
                    msg = _build_context_aware_response(
                        directive="REQUIRE_DATE_PICKER",
                        field_meta=None,
                        collected_data=chat_session.collected_data,
                        valid_data=valid_data,
                        errors={},
                        llm_generated_msg=llm_generated_msg,
                        agent_name=persona.get("agent_name", "Booking Assistant"),
                    )
            except Exception as slot_err:
                logger.warning(f"Failed to compute time slots: {slot_err}")
                field_meta["options"] = []
                field_meta["availability_status"] = "no_schedule"
                directive = "REQUIRE_DATE_PICKER"
                msg = _build_context_aware_response(
                    directive="REQUIRE_DATE_PICKER",
                    field_meta=None,
                    collected_data=chat_session.collected_data,
                    valid_data=valid_data,
                    errors={},
                    llm_generated_msg=llm_generated_msg,
                    agent_name=persona.get("agent_name", "Booking Assistant"),
                )

    from app.engine.user_visible_text import looks_like_confirmation_claim

    if (
        looks_like_confirmation_claim(msg)
        and directive != "BOOKING_CONFIRMED_CARD"
        and str(chat_session.session_status).upper() != "CONFIRMED"
    ):
        msg = _build_context_aware_response(
            directive=directive,
            field_meta=field_meta,
            collected_data=chat_session.collected_data,
            valid_data={},
            errors={},
            llm_generated_msg=msg,
            agent_name=persona.get("agent_name", "Booking Assistant"),
        )

    action = ActionPayload(
        message=msg, widget_directive=directive, field_meta=field_meta
    )

    # Persist transcript (best-effort)
    await _save_transcript(
        db,
        chat_session.session_id,
        message_text,
        msg,
        directive,
        intent=chat_session.current_intent,
    )

    return action


# ══════════════════════════════════════════════════════════════
#  Private helpers
# ══════════════════════════════════════════════════════════════


async def _handle_otp_resend(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    from app.crud.otp import generate_otp, get_otp_policy_metadata, OTP_RESEND_CAP
    from app.core.mail import send_otp_email

    try:
        recipient_email = _resolve_session_email(chat_session)
        code = await generate_otp(db, chat_session.session_id, resend=True)
        policy_meta = await get_otp_policy_metadata(db, chat_session.session_id)
        resend_attempt = OTP_RESEND_CAP - int(policy_meta.get("resends_left", 0))
        otp_send_key = f"otp:{chat_session.session_id}:resend:{resend_attempt}"
        if background_tasks:
            background_tasks.add_task(
                send_otp_email,
                recipient_email,
                code,
                org_id=org.id,
                idempotency_key=otp_send_key,
            )
        else:
            await send_otp_email(
                recipient_email,
                code,
                org_id=org.id,
                idempotency_key=otp_send_key,
            )
        action = ActionPayload(
            message=f"I've sent a new verification code to {recipient_email}. Please check your inbox.",
            widget_directive="REQUIRE_OTP_MODAL",
            field_meta=policy_meta,
        )
    except ValueError as otp_err:
        action = ActionPayload(
            message=str(otp_err),
            widget_directive="REQUIRE_OTP_MODAL",
            field_meta=await get_otp_policy_metadata(db, chat_session.session_id),
        )
    except Exception as otp_err:
        logger.error(f"OTP resend failed: {otp_err}")
        action = ActionPayload(
            message="I'm still having trouble sending the code. Please verify your email address is correct.",
            widget_directive="TEXT_INPUT",
        )
    await _save_transcript(
        db,
        chat_session.session_id,
        message_text,
        action.message,
        action.widget_directive,
    )
    return action


async def _handle_booking_flow(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    intent: str,
    require_otp: bool,
    fd_map: Dict[str, FieldDefinition],
    background_tasks: Optional[BackgroundTasks] = None,
    *,
    dispatch_notifications: bool = True,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Handle the PROCESS_BOOKING directive — OTP, confirmation, slot booking."""

    # Lock the slot BEFORE showing OTP modal or confirmation request
    # This prevents ghost slots during the waiting period
    await _lock_slot_for_booking(db, org, chat_session)

    # OTP gate (if enabled and not yet verified)
    recipient_email = _resolve_session_email(chat_session, fd_map)

    if require_otp:
        otp_context_key = _build_otp_context_key(chat_session, fd_map)
        internal_state = _ensure_internal_state(chat_session)
        verified_key = str(internal_state.get("otp_verified_key") or "")
        if chat_session.otp_verified and verified_key != otp_context_key:
            chat_session.otp_verified = False
            internal_state.pop("otp_verified_key", None)
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    if require_otp and not chat_session.otp_verified:
        from app.crud.otp import generate_otp, get_otp_policy_metadata
        from app.core.mail import send_otp_email
        otp_send_key = _build_otp_context_key(chat_session, fd_map)
        internal_state = _ensure_internal_state(chat_session)
        if internal_state.get("otp_send_key") == otp_send_key:
            return (
                f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. Please enter it here to continue.",
                "REQUIRE_OTP_MODAL",
                await get_otp_policy_metadata(db, chat_session.session_id),
            )

        try:
            code = await generate_otp(db, chat_session.session_id, resend=False)
            if background_tasks:
                background_tasks.add_task(
                    send_otp_email,
                    recipient_email,
                    code,
                    org_id=org.id,
                    idempotency_key=otp_send_key,
                )
            else:
                await send_otp_email(
                    recipient_email,
                    code,
                    org_id=org.id,
                    idempotency_key=otp_send_key,
                )
            internal_state["otp_send_key"] = otp_send_key
            internal_state.pop("otp_verified_key", None)
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
            return (
                f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. Please enter it here to continue.",
                "REQUIRE_OTP_MODAL",
                await get_otp_policy_metadata(db, chat_session.session_id),
            )
        except Exception as otp_err:
            logger.error(f"OTP email failed: {otp_err}")
            return (
                "I couldn't send the verification code to your email. Please double-check your email address and try again, or say 'resend' to retry.",
                "TEXT_INPUT",
                None,
            )

    # OTP verified (or disabled) — proceed to booking
    if chat_session.session_status != "ACTIVE":
        return "Your booking is already processed.", "TEXT_INPUT", None

    if intent == "CONFIRM_BOOKING":
        msg, directive, booking_field_meta = await _execute_booking(
            db,
            org,
            chat_session,
            fd_map,
            background_tasks,
            dispatch_notifications=dispatch_notifications,
        )
        retrying = "couldn't send the confirmation email" in msg.lower()
        if retrying:
            booking_field_meta = dict(booking_field_meta or {})
            booking_field_meta["email_retrying"] = True
        return msg, directive, booking_field_meta
    else:
        summary_msg, summary_directive = _build_summary_card(chat_session, fd_map)
        return summary_msg, summary_directive, None


# ── Post-Booking Handler ─────────────────────────────────────────

CANCEL_KEYWORDS = [
    "cancel my booking",
    "cancel it",
    "i want to cancel",
    "please cancel",
    "cancel the booking",
    "cancel appointment",
    "undo booking",
    "revoke",
    "withdraw",
]

# Softer cancel-adjacent phrases — these need confirmation before cancelling
CANCEL_SOFT_KEYWORDS = [
    "cancel",
    "don't want",
    "dont want",
    "changed my mind",
    "not coming",
]

# Questions asking for cancellation guidance should not trigger cancellation flow.
CANCEL_INFO_KEYWORDS = [
    "procedure",
    "process",
    "how to cancel",
    "how do i cancel",
    "how can i cancel",
    "cancellation policy",
    "cancel policy",
    "steps to cancel",
    "procedure to cancel",
]

QUESTION_HINTS = [
    "?",
    "how",
    "what",
    "when",
    "why",
    "can i",
    "could i",
    "should i",
    "will i",
    "procedure",
    "process",
    "policy",
    "steps",
]

RESCHEDULE_KEYWORDS = [
    "reschedule",
    "change date",
    "change time",
    "change the date",
    "change the time",
    "wrong date",
    "wrong time",
    "different date",
    "different time",
    "move my booking",
    "move it",
    "shift",
    "update date",
    "update time",
    "not available on",
]

RESCHEDULE_INFO_KEYWORDS = [
    "reschedule procedure",
    "procedure to reschedule",
    "how to reschedule",
    "how do i reschedule",
    "how can i reschedule",
    "rescheduling process",
    "reschedule process",
    "rescheduling policy",
    "reschedule policy",
    "steps to reschedule",
    "rescheduling",
    "rescedule",
    "resceduling",
    "rescedulting",
    "reshedule",
    "reschedule",
]

REBOOK_KEYWORDS = [
    "book",
    "new booking",
    "rebook",
    "schedule",
    "appointment",
    "reserve",
    "start",
    "yes",
    "yep",
    "yup",
    "sure",
    "okay",
    "ok",
    "alright",
    "absolutely",
    "go ahead",
    "let's go",
    "i want to",
    "i'd like to",
    "please",
    "meeting",
    "session",
    "initiate",
    "begin",
]


def _contains_any(msg: str, phrases: list[str]) -> bool:
    return any(p in msg for p in phrases)


def _looks_like_question(msg: str) -> bool:
    if "?" in msg:
        return True
    stripped = msg.strip()
    return _contains_any(stripped, QUESTION_HINTS)


async def _llm_post_booking_self_check(
    message_text: str, status: str, pending_cancel: bool
) -> dict:
    """
    Lightweight semantic classifier used only as a secondary signal.
    Deterministic policy remains the hard safety guard.
    """
    prompt = f"""Classify the user message in booking support context.

SESSION_STATUS: {status}
PENDING_CANCEL_CONFIRMATION: {pending_cancel}
USER_MESSAGE: "{message_text}"

Return ONLY JSON with:
{{
  "classification": "INFO_QUESTION|ACTION_REQUEST|CONFIRM|DENY|AMBIGUOUS|NEUTRAL",
  "action": "CANCEL|RESCHEDULE|NONE",
  "confidence": 0.0
}}
"""
    try:
        result = await llm_engine._call_llm(
            prompt,
            system_prompt="You are a strict intent classifier. Output JSON only.",
            json_mode=True,
        )
        if not result:
            return {"classification": "NEUTRAL", "action": "NONE", "confidence": 0.0}
        import json as _json

        payload = _json.loads(result)
        classification = str(payload.get("classification", "NEUTRAL")).upper()
        action = str(payload.get("action", "NONE")).upper()
        confidence = float(payload.get("confidence", 0.0))
        return {
            "classification": classification,
            "action": action,
            "confidence": confidence,
        }
    except Exception:
        return {"classification": "NEUTRAL", "action": "NONE", "confidence": 0.0}


async def _handle_post_booking(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """
    Handle user messages AFTER a booking is confirmed, cancelled, or expired.
    Supports: cancel, reschedule, restart new booking, conversational replies.
    """
    user_msg_lower = message_text.lower().strip()
    status = chat_session.session_status
    self_check = await _llm_post_booking_self_check(
        message_text=message_text,
        status=status,
        pending_cancel=bool(chat_session.pending_cancel),
    )

    def _is_cancellation_info_request(msg: str) -> bool:
        has_cancel_context = "cancel" in msg or "cancellation" in msg
        has_info_intent = any(kw in msg for kw in CANCEL_INFO_KEYWORDS)
        return has_cancel_context and has_info_intent

    def _is_reschedule_info_request(msg: str) -> bool:
        has_reschedule_context = any(kw in msg for kw in RESCHEDULE_INFO_KEYWORDS)
        has_info_intent = _looks_like_question(msg) or any(
            kw in msg for kw in ["procedure", "process", "policy", "steps"]
        )
        return has_reschedule_context and has_info_intent

    is_question = _looks_like_question(user_msg_lower)

    # ── Reschedule a CONFIRMED booking (cancel + reopen with existing data) ──
    if status == "CONFIRMED" and _is_reschedule_info_request(user_msg_lower):
        msg = (
            "Sure — here's the rescheduling procedure:\n"
            "1) Say **'reschedule my booking'**\n"
            "2) I'll reopen your booking flow while keeping your name/email/phone\n"
            "3) You'll choose a new date and time, then confirm.\n\n"
            "No changes have been made to your current booking yet."
        )
        action = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", is_terminal=False
        )
        await _save_transcript(
            db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
        )
        return action

    if status == "CONFIRMED" and (
        (not is_question and _contains_any(user_msg_lower, RESCHEDULE_KEYWORDS))
        or (
            self_check.get("classification") == "ACTION_REQUEST"
            and self_check.get("action") == "RESCHEDULE"
            and float(self_check.get("confidence", 0.0)) >= 0.70
            and not is_question
        )
    ):
        return await _reschedule_booking(db, org, chat_session, background_tasks)

    # ── Cancel a CONFIRMED booking (always ask for confirmation) ──────
    if status == "CONFIRMED":
        explicit_cancel = _contains_any(user_msg_lower, CANCEL_KEYWORDS)
        soft_cancel = _contains_any(user_msg_lower, CANCEL_SOFT_KEYWORDS)
        inferred_cancel_action = (
            self_check.get("classification") == "ACTION_REQUEST"
            and self_check.get("action") == "CANCEL"
            and float(self_check.get("confidence", 0.0)) >= 0.75
        )

        # Check if user is confirming a previously asked cancel
        pending_cancel = chat_session.pending_cancel
        if pending_cancel:
            chat_session.pending_cancel = False
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )

            affirm = any(
                w in user_msg_lower
                for w in [
                    "yes",
                    "yep",
                    "yup",
                    "sure",
                    "confirm",
                    "go ahead",
                    "absolutely",
                    "ok",
                    "okay",
                    "do it",
                    "cancel",
                ]
            )
            deny = any(
                w in user_msg_lower
                for w in [
                    "no",
                    "nah",
                    "nope",
                    "not now",
                    "don't",
                    "dont",
                    "keep it",
                    "keep booking",
                ]
            )
            if _is_cancellation_info_request(user_msg_lower):
                msg = (
                    "Great question — your booking is still active. "
                    "To cancel, just say **'cancel my booking'** and I'll ask for a final yes/no confirmation "
                    "before cancelling. Once confirmed, the slot is released immediately."
                )
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
                )
                return action
            if deny:
                msg = "No worries — your booking stays as is! Let me know if you need anything else. 😊"
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
                )
                return action
            if affirm:
                return await _cancel_confirmed_booking(
                    db, org, chat_session, background_tasks
                )
            if is_question or self_check.get("classification") in {
                "INFO_QUESTION",
                "AMBIGUOUS",
            }:
                msg = (
                    "Your booking is still active. If you want to cancel it, reply **yes**. "
                    "If you only want the steps, say **'show cancellation steps'**."
                )
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
                )
                return action
            else:
                msg = "No worries — your booking stays as is! Let me know if you need anything else. 😊"
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
                )
                return action

        if _is_cancellation_info_request(user_msg_lower) or (
            is_question
            and (
                "cancel" in user_msg_lower
                or (
                    self_check.get("classification") == "INFO_QUESTION"
                    and self_check.get("action") == "CANCEL"
                )
            )
        ):
            msg = (
                "Sure — here's the cancellation procedure:\n"
                "1) Say **'cancel my booking'**\n"
                "2) I'll ask for confirmation (**yes/no**)\n"
                "3) Reply **yes** to cancel, or **no** to keep your booking.\n\n"
                "No changes have been made to your current booking."
            )
            action = ActionPayload(
                message=msg, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _save_transcript(
                db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
            )
            return action

        if explicit_cancel or soft_cancel or inferred_cancel_action:
            # Set pending flag and ask for confirmation
            chat_session.pending_cancel = True
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            msg = "Are you sure you want to cancel your booking? This will release your reserved time slot. Reply **yes** to confirm or **no** to keep it."
            action = ActionPayload(
                message=msg, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _save_transcript(
                db, chat_session.session_id, message_text, msg, "TEXT_INPUT"
            )
            return action

    # ── Restart booking from CANCELLED/EXPIRED ─────────────────
    if status in ("CANCELLED", "EXPIRED"):
        wants_to_rebook = any(kw in user_msg_lower for kw in REBOOK_KEYWORDS)
        if wants_to_rebook:
            from app.engine.cancel_flow import (
                _persist_session,
                SESSION_RESET_REMOVE_KEYS,
                reset_for_new_booking,
            )

            reset_for_new_booking(chat_session)
            await _persist_session(
                db, chat_session, remove_keys=SESSION_RESET_REMOVE_KEYS
            )
            return await process_message(
                db, org, org_config, chat_session, message_text
            )

    # ── For non-actionable post-booking messages, respond with guardrails ──
    persona = (
        org_config.custom_prompts if isinstance(org_config.custom_prompts, dict) else {}
    )
    agent_name = persona.get("agent_name", "Booking Assistant")
    tone = persona.get("tone", "professional and friendly")

    industry_context = persona.get("industry_context", "")
    custom_instructions = persona.get("custom_instructions", "")
    company_context_block = ""
    if industry_context or custom_instructions:
        company_context_block = (
            f"\n### COMPANY CONTEXT (you may answer questions about this):\n"
        )
        if industry_context:
            company_context_block += f"Industry: {industry_context}\n"
        if custom_instructions:
            company_context_block += f"Details: {custom_instructions}\n"

    status_context = {
        "CONFIRMED": f"The user's booking is CONFIRMED (details: {__import__('json').dumps(chat_session.collected_data)}). They may ask to cancel, reschedule, or have questions.",
        "CANCELLED": "The user's previous booking was CANCELLED. They may want to start a new booking.",
        "EXPIRED": "The user's session EXPIRED. They may want to start a new booking.",
    }

    post_booking_prompt = f"""You are {agent_name}, a {tone} AI booking assistant.

### SITUATION:
{status_context.get(status, "Session is no longer active.")}
{company_context_block}
### USER'S MESSAGE:
"{message_text}"

### RULES:
- You are a booking assistant. You can answer questions about bookings, appointments, scheduling, AND any company/service context provided above.
- If the user asks about something covered by the company context (services, hours, pricing, etc.), answer helpfully.
- If the user asks something completely unrelated (general knowledge, trivia, personal questions like "what is a paper plane"), politely say that's outside your area and gently steer back: "That's a bit outside my expertise! I'm here to help with bookings — is there anything about your appointment I can help with?"
- If they want to CANCEL a confirmed booking: Ask them to confirm by saying "cancel my booking".
- If they want to RESCHEDULE: Tell them to cancel first, then start a new booking.
- If they say THANKS or GOODBYE: Respond warmly.
- If session is CANCELLED/EXPIRED and they want to rebook: Encourage them to say "I want to book".
- Keep responses to 1-2 sentences max.
- Do NOT invent information you don't have.

Output ONLY a valid JSON object:
{{"response_message": "your reply here"}}

Output ONLY valid JSON:"""

    try:
        result = await llm_engine._call_llm(post_booking_prompt)
        import json as _json

        parsed = _json.loads(result)
        msg = parsed.get(
            "response_message",
            "Is there anything else I can help you with regarding your booking?",
        )
    except Exception:
        fallback = {
            "CONFIRMED": "Your booking is confirmed! You can say 'cancel my booking' to cancel, or start a new booking anytime.",
            "CANCELLED": "Your previous booking was cancelled. Just say 'I want to book' to start a new one!",
            "EXPIRED": "This session expired. Say 'I want to book' to start a new booking!",
        }
        msg = fallback.get(status, "Is there anything else I can help you with?")

    action = ActionPayload(
        message=msg, widget_directive="TEXT_INPUT", is_terminal=False
    )
    await _save_transcript(db, chat_session.session_id, message_text, msg, "TEXT_INPUT")
    return action


async def _reschedule_booking(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """
    Reschedule = cancel the slot + keep user data + re-enter booking flow.
    Only date and time are cleared so user doesn't have to re-enter name/email/phone.
    """
    from app.crud.booking import cancel_booking_by_session

    cancelled = await cancel_booking_by_session(
        db, chat_session.session_id, org_id=org.id
    )
    customer_name = chat_session.collected_data.get("name", "there")
    customer_email = _resolve_session_email(chat_session)

    # Keep personal data, clear only date/time and stale slot reference
    chat_session.collected_data.pop("date", None)
    chat_session.collected_data.pop("time", None)
    chat_session.collected_data.pop("selected_slot_id", None)
    chat_session.session_status = "ACTIVE"
    chat_session.current_intent = "EDIT_DATA"
    chat_session.otp_verified = False
    chat_session.missing_fields = ["date", "time"]
    from app.engine.cancel_flow import _persist_session, SESSION_RESET_REMOVE_KEYS

    await _persist_session(db, chat_session, remove_keys=SESSION_RESET_REMOVE_KEYS)

    if cancelled:
        if background_tasks:
            log_audit_event(
                background_tasks,
                org_id=org.id,
                actor_id=None,
                actor_email=customer_email,
                action=AuditAction.BOOKING_UPDATE,
                resource_type="booking",
                resource_id=chat_session.session_id,
                old_values={
                    "session_status": "CONFIRMED",
                    "date": chat_session.collected_data.get("date"),
                    "time": chat_session.collected_data.get("time"),
                },
                new_values={
                    "session_status": "ACTIVE",
                    "date": None,
                    "time": None,
                    "intent": "EDIT_DATA",
                },
                ip_address=None,
                user_agent=None,
            )
        msg = (
            f"No problem, {customer_name}! I've freed up your previous slot. "
            f"Let's pick a new date — when would you like to reschedule to?"
        )
    else:
        msg = (
            f"Sure thing, {customer_name}! What date and time would you prefer instead?"
        )

    action = ActionPayload(
        message=msg, widget_directive="REQUIRE_DATE_PICKER", is_terminal=False
    )
    await _save_transcript(
        db,
        chat_session.session_id,
        "reschedule",
        msg,
        "REQUIRE_DATE_PICKER",
        intent="EDIT_DATA",
    )
    return action


async def _cancel_confirmed_booking(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """Cancel a confirmed booking: release the slot and update session status."""
    from app.crud.booking import cancel_booking_by_session

    booking_result = await db.execute(
        select(Booking).where(
            Booking.session_id == chat_session.session_id,
            Booking.org_id == org.id,
        )
    )
    existing_booking = booking_result.scalars().first()

    cancelled = await cancel_booking_by_session(
        db, chat_session.session_id, org_id=org.id
    )
    chat_session.session_status = "CANCELLED"
    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
    customer_email = _resolve_session_email(chat_session)

    if cancelled:
        from app.core.org_notify import notify_org

        await notify_org(
            org_id=org.id,
            kind="booking_cancelled",
            title="Appointment cancelled",
            body=(
                f"{chat_session.collected_data.get('name') or 'A customer'} cancelled "
                f"their appointment on {chat_session.collected_data.get('date') or 'an unknown date'} "
                f"at {chat_session.collected_data.get('time') or ''}".strip()
                + "."
            ),
            resource_type="booking",
            resource_id=str(existing_booking.id) if existing_booking else None,
        )
        if background_tasks:
            log_audit_event(
                background_tasks,
                org_id=org.id,
                actor_id=None,
                actor_email=customer_email,
                action=AuditAction.BOOKING_CANCEL,
                resource_type="booking",
                resource_id=chat_session.session_id,
                old_values={
                    "session_status": "CONFIRMED",
                    "date": chat_session.collected_data.get("date"),
                    "time": chat_session.collected_data.get("time"),
                },
                new_values={
                    "session_status": "CANCELLED",
                    "date": chat_session.collected_data.get("date"),
                    "time": chat_session.collected_data.get("time"),
                },
                ip_address=None,
                user_agent=None,
            )
        customer_name = chat_session.collected_data.get("name", "there")
        msg = (
            f"Done, {customer_name} — your booking has been cancelled and the time slot has been released. "
            f"If you change your mind, feel free to start a new booking anytime! 😊"
        )
        cancellation_email_failed = False
        # Send cancellation email (best-effort)
        try:
            from app.core.mail import send_cancellation_email

            if customer_email:
                booking_details = {
                    "name": chat_session.collected_data.get("name"),
                    "email": customer_email,
                    "date": chat_session.collected_data.get("date"),
                    "time": chat_session.collected_data.get("time"),
                }
                if existing_booking and existing_booking.meeting_type_snapshot:
                    booking_details["meeting_type_snapshot"] = (
                        existing_booking.meeting_type_snapshot
                    )
                if existing_booking and existing_booking.meeting_link_status:
                    booking_details["meeting_link_status"] = (
                        existing_booking.meeting_link_status.value
                    )
                if existing_booking and existing_booking.meeting_link_url:
                    booking_details["meeting_link_url"] = (
                        existing_booking.meeting_link_url
                    )
                if existing_booking and existing_booking.meeting_link_provider:
                    booking_details["meeting_link_provider"] = (
                        existing_booking.meeting_link_provider.value
                    )
                if existing_booking and existing_booking.user_timezone:
                    booking_details["user_timezone"] = existing_booking.user_timezone
                if existing_booking and existing_booking.org_timezone_snapshot:
                    booking_details["org_timezone_snapshot"] = (
                        existing_booking.org_timezone_snapshot
                    )

                await send_cancellation_email(
                    customer_email,
                    customer_name,
                    booking_details,
                    org_id=org.id,
                )
        except Exception as mail_err:
            logger.warning(f"Cancellation email failed: {mail_err}")
            cancellation_email_failed = True
        if cancellation_email_failed:
            msg += " Your cancellation is complete, but I couldn't send the cancellation email right now."
    else:
        msg = "It looks like there's no active booking to cancel. You can start a new booking anytime!"

    action = ActionPayload(
        message=msg, widget_directive="TEXT_INPUT", is_terminal=False
    )
    await _save_transcript(
        db,
        chat_session.session_id,
        "cancel booking",
        msg,
        "TEXT_INPUT",
        intent="CANCEL_BOOKING",
    )
    return action


async def _execute_booking(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    fd_map: Dict[str, FieldDefinition],
    background_tasks: Optional[BackgroundTasks] = None,
    *,
    dispatch_notifications: bool = True,
) -> Tuple[str, str]:
    """Atomic slot booking with SELECT FOR UPDATE."""
    try:
        from app.crud.booking import (
            get_available_slots,
            attempt_booking,
            get_next_available_dates,
        )
        from dateutil.parser import parse as date_parse

        # Atomic ACTIVE → CONFIRMING
        got_lock = await crud_chat.atomic_set_confirming(db, chat_session.session_id)
        if not got_lock:
            return (
                "Your booking is already being processed. Please wait a moment...",
                "TEXT_INPUT",
                None,
            )
        chat_session.session_status = "CONFIRMING"
        customer_email = _resolve_session_email(chat_session, fd_map)

        date_str = chat_session.collected_data.get("date")
        if not date_str:
            logger.error(
                f"Missing date in collected_data for session {chat_session.session_id}"
            )
            return (
                "Something went wrong (missing date). Please restart the booking process.",
                "TEXT_INPUT",
                None,
            )
        target_date = date_parse(date_str).date()
        requested_time = chat_session.collected_data.get("time")

        # In the virtual slot model, the lock step already created/found the TimeSlot row.
        # We just need to find that lock row by org + exact start_time and confirm it.
        from app.engine.slot_engine import compute_available_slots, format_slot_time
        from dateutil.parser import parse as req_parse

        try:
            from zoneinfo import ZoneInfo
        except ImportError:  # pragma: no cover
            from backports.zoneinfo import ZoneInfo

        req_time = None
        if requested_time and not _is_date_only_value(str(requested_time)):
            req_time = req_parse(requested_time).time()

        # Find the lock row created by _lock_slot_for_booking.
        # Use lock metadata first, then selected-slot metadata, then date+time fallback.
        from app.models.booking import TimeSlot, BusinessSchedule
        from sqlalchemy.future import select as sa_select

        target_slot = None
        lock_slot_id = chat_session.collected_data.get("locked_slot_id")
        lock_slot_start_utc = _resolve_lock_slot_start_utc(
            chat_session.collected_data.get("locked_slot_start_utc")
        )
        selected_slot_start_utc = _resolve_selected_slot_start_utc(
            chat_session.collected_data.get("selected_slot_id"),
            str(org.id),
        )

        if lock_slot_id:
            lock_by_id_result = await db.execute(
                sa_select(TimeSlot).where(
                    TimeSlot.id == lock_slot_id,
                    TimeSlot.org_id == org.id,
                    TimeSlot.is_booked == False,
                )
            )
            target_slot = lock_by_id_result.scalars().first()
            if target_slot and lock_slot_start_utc:
                candidate_start = (
                    target_slot.start_time.replace(tzinfo=timezone.utc)
                    if target_slot.start_time.tzinfo is None
                    else target_slot.start_time.astimezone(timezone.utc)
                )
                if candidate_start != lock_slot_start_utc:
                    logger.info(
                        "Lock slot id start mismatch; discarding stale lock slot metadata",
                        extra={
                            "session_id": chat_session.session_id,
                            "locked_slot_start_utc": lock_slot_start_utc.isoformat(),
                            "candidate_start_utc": candidate_start.isoformat(),
                        },
                    )
                    target_slot = None

        if not target_slot and lock_slot_start_utc:
            lock_by_start_result = await db.execute(
                sa_select(TimeSlot).where(
                    TimeSlot.org_id == org.id,
                    TimeSlot.start_time == lock_slot_start_utc,
                    TimeSlot.is_booked == False,
                )
            )
            target_slot = lock_by_start_result.scalars().first()

        if not target_slot and selected_slot_start_utc:
            selected_slot_result = await db.execute(
                sa_select(TimeSlot).where(
                    TimeSlot.org_id == org.id,
                    TimeSlot.start_time == selected_slot_start_utc,
                    TimeSlot.is_booked == False,
                )
            )
            target_slot = selected_slot_result.scalars().first()

        if not target_slot and req_time:
            schedule_result = await db.execute(
                sa_select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
            )
            schedule = schedule_result.scalars().first()
            business_tz = (
                schedule.timezone
                if schedule and getattr(schedule, "timezone", None)
                else "UTC"
            )
            try:
                business_zone = ZoneInfo(str(business_tz))
            except Exception:
                business_zone = ZoneInfo("UTC")

            start_dt = datetime.combine(
                target_date, req_time, tzinfo=business_zone
            ).astimezone(timezone.utc)
            lock_result = await db.execute(
                sa_select(TimeSlot).where(
                    TimeSlot.org_id == org.id,
                    TimeSlot.start_time == start_dt,
                    TimeSlot.is_booked == False,
                )
            )
            target_slot = lock_result.scalars().first()

        if not target_slot:
            # No lock row found — check if any slots available to suggest alternatives
            available_times = await compute_available_slots(db, org.id, target_date)
            chat_session.session_status = "ACTIVE"
            alt_dates = await get_next_available_dates(
                db, str(org.id), target_date, limit=3
            )
            if alt_dates:
                date_strs = ", ".join(d.isoformat() for d in alt_dates)
                msg = f"I'm sorry, there are no available slots on that date. However, I found openings on: {date_strs}. Would you like to pick one of those?"
            elif available_times:
                opts = ", ".join(format_slot_time(t) for t in available_times[:5])
                msg = f"I'm sorry, that time slot isn't available. Here are available times: {opts}. Which works for you?"
            else:
                msg = "I'm sorry, there are no available slots on that date. Could you please try a different date?"
            updated_data = {
                k: v
                for k, v in chat_session.collected_data.items()
                if k not in ("time", "date")
            }
            chat_session.collected_data = updated_data
            _clear_lock_slot_metadata(chat_session)
            chat_session.missing_fields = ["date", "time"]
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            return msg, "REQUIRE_DATE_PICKER", None

        policy_result = await db.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        live_policy = policy_result.scalars().first()
        mode = _normalize_meeting_mode(
            live_policy.meeting_mode
            if live_policy and getattr(live_policy, "meeting_mode", None)
            else MeetingMode.OFFLINE_ONLY
        )
        allow_hybrid = (
            bool(live_policy.allow_hybrid_selection)
            if live_policy and hasattr(live_policy, "allow_hybrid_selection")
            else True
        )
        allowed_types = _allowed_meeting_types(mode, allow_hybrid)
        requested_meeting_type = _normalize_meeting_type(
            chat_session.collected_data.get("meeting_type")
        )

        if requested_meeting_type and requested_meeting_type not in allowed_types:
            chat_session.session_status = "ACTIVE"
            chat_session.current_intent = "CONFIRM_BOOKING"
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            allowed_text = " or ".join(allowed_types).lower()
            return (
                f"I can't confirm this booking because your organization currently allows {allowed_text} meetings only. "
                f"Please choose one of the allowed options and confirm again.",
                "TEXT_INPUT",
                None,
            )

        is_online_request = requested_meeting_type == "ONLINE"
        active_provider = _normalize_meeting_provider(
            live_policy.meeting_provider_default
            if live_policy and getattr(live_policy, "meeting_provider_default", None)
            else MeetingProviderDefault.NONE
        )
        has_online_path = (
            bool(live_policy and live_policy.google_calendar_connected)
            or bool(live_policy and live_policy.outlook_calendar_connected)
            or active_provider != MeetingProviderDefault.NONE
        )
        if is_online_request and not has_online_path:
            chat_session.session_status = "ACTIVE"
            chat_session.current_intent = "CONFIRM_BOOKING"
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            return (
                "I can't finalize an online meeting right now because no online meeting provider is available. "
                "Please retry shortly or choose an offline meeting option.",
                "TEXT_INPUT",
                None,
            )

        org_timezone = (
            str(live_policy.timezone) if live_policy and live_policy.timezone else "UTC"
        )
        timezone_resolution = resolve_effective_timezone_policy(
            requested_tz=chat_session.collected_data.get("user_timezone"),
            org_tz=org_timezone,
            requested_source=chat_session.collected_data.get("timezone_source"),
        )
        effective_user_tz = str(timezone_resolution["effective_timezone"])
        fallback_used = bool(timezone_resolution["fallback_used"])
        fallback_reason = timezone_resolution["fallback_reason"]
        user_timezone_source = str(timezone_resolution["timezone_source"])

        try:
            org_zone = ZoneInfo(org_timezone)
            org_timezone_snapshot = org_zone.key
        except Exception:
            org_zone = ZoneInfo("UTC")
            org_timezone_snapshot = "UTC"

        user_zone = ZoneInfo(effective_user_tz)

        canonical_start_utc = target_slot.start_time
        if canonical_start_utc.tzinfo is None:
            canonical_start_utc = canonical_start_utc.replace(tzinfo=timezone.utc)
        else:
            canonical_start_utc = canonical_start_utc.astimezone(timezone.utc)

        canonical_end_utc = target_slot.end_time
        if canonical_end_utc is None:
            canonical_end_utc = canonical_start_utc + timedelta(hours=1)
        elif canonical_end_utc.tzinfo is None:
            canonical_end_utc = canonical_end_utc.replace(tzinfo=timezone.utc)
        else:
            canonical_end_utc = canonical_end_utc.astimezone(timezone.utc)

        if selected_slot_start_utc and selected_slot_start_utc != canonical_start_utc:
            logger.info(
                "Selected slot metadata does not match lock row; using canonical lock row",
                extra={
                    "session_id": chat_session.session_id,
                    "selected_slot_start_utc": selected_slot_start_utc.isoformat(),
                    "canonical_start_utc": canonical_start_utc.isoformat(),
                },
            )

        user_slot_start_at = canonical_start_utc.astimezone(user_zone)
        user_slot_end_at = canonical_end_utc.astimezone(user_zone)
        org_slot_start_at = canonical_start_utc.astimezone(org_zone)
        org_slot_end_at = canonical_end_utc.astimezone(org_zone)

        same_day_policy = evaluate_same_day_policy(
            target_date=target_date,
            now_utc=datetime.now(timezone.utc),
            effective_timezone=effective_user_tz,
            allow_same_day_booking=bool(
                live_policy.allow_same_day_booking
                if live_policy and hasattr(live_policy, "allow_same_day_booking")
                else False
            ),
        )
        if not same_day_policy["allowed"]:
            chat_session.session_status = "ACTIVE"
            chat_session.collected_data.pop("date", None)
            chat_session.collected_data.pop("time", None)
            chat_session.collected_data.pop("selected_slot_id", None)
            _clear_lock_slot_metadata(chat_session)
            chat_session.missing_fields = ["date", "time"]
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
            return (
                "Same-day booking isn't available for this business. Please choose a future date.",
                "REQUIRE_DATE_PICKER",
                {"policy_reason_code": "same_day_disabled"},
            )

        cutoff_policy = evaluate_slot_cutoff_policy(
            slot_start_utc=canonical_start_utc,
            now_utc=datetime.now(timezone.utc),
            effective_timezone=effective_user_tz,
            slot_cutoff_minutes=(
                live_policy.slot_cutoff_minutes
                if live_policy and hasattr(live_policy, "slot_cutoff_minutes")
                else 5
            ),
        )
        if not cutoff_policy["allowed"]:
            chat_session.session_status = "ACTIVE"
            chat_session.collected_data.pop("time", None)
            chat_session.collected_data.pop("selected_slot_id", None)
            _clear_lock_slot_metadata(chat_session)
            chat_session.missing_fields = ["time"]
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
            return (
                "That time has already passed in your selected timezone. Please pick a later slot.",
                "REQUIRE_TIME_SLOT_GRID",
                {
                    "policy_reason_code": "past_time_cutoff",
                    "cutoff_minutes": cutoff_policy["cutoff_minutes"],
                },
            )

        # --- Meeting Link Generation (Phase 16-02) ---
        # D-09/D-10: Only ONLINE_ONLY triggers link generation (HYBRID stays offline-only)
        from app.models.config import MeetingLinkStatus, ActiveMeetingProvider
        from app.core.integrations.meeting_providers.factory import (
            generate_meeting_link,
        )

        meeting_type_snapshot = None
        meeting_link_url = None
        meeting_link_provider = None
        meeting_link_status = MeetingLinkStatus.NOT_REQUIRED
        meeting_link_external_id = None
        meeting_link_error_code = None
        meeting_link_degraded = False

        # Determine if this is an ONLINE booking that requires link generation
        is_online_only_mode = mode == MeetingMode.ONLINE_ONLY
        user_chose_online = requested_meeting_type == "ONLINE"
        configured_active_provider = (
            live_policy.active_meeting_provider
            if live_policy and getattr(live_policy, "active_meeting_provider", None)
            else None
        )

        if is_online_only_mode:
            # ONLINE_ONLY mode: always generate link, snapshot as ONLINE
            meeting_type_snapshot = "ONLINE"
            link_result = await generate_meeting_link(
                config=live_policy,
                db=db,
                booking_id=str(target_slot.id),  # Use slot ID as interim identifier
                start_time=canonical_start_utc,
                end_time=canonical_end_utc,
                summary=f"Booking with {chat_session.collected_data.get('name', 'Customer')}",
                attendee_email=customer_email,
            )
            if link_result.is_success:
                meeting_link_url = link_result.meeting_link_url
                meeting_link_external_id = link_result.external_id
                meeting_link_status = MeetingLinkStatus.READY
                meeting_link_provider = live_policy.active_meeting_provider
            else:
                # D-01: Provider failure → booking still confirmed with degraded state
                # D-02: Persist meeting_link_status='UNAVAILABLE' on degraded confirmations
                meeting_link_status = MeetingLinkStatus.UNAVAILABLE
                meeting_link_error_code = (
                    link_result.error_code.value if link_result.error_code else None
                )
                meeting_link_degraded = True
                logger.warning(
                    f"Meeting link generation failed (degraded): {link_result.error_message}",
                    extra={
                        "session_id": chat_session.session_id,
                        "error_code": meeting_link_error_code,
                    },
                )
                try:
                    request_id = (
                        correlation_id.get()
                        or chat_session.collected_data.get("request_id")
                    )
                    trace_id = chat_session.collected_data.get("trace_id")
                    await create_system_alert(
                        db,
                        SystemAlertCreate(
                            org_id=org.id,
                            component=AlertComponent.BOOKING,
                            severity=AlertSeverity.WARNING,
                            code="ONLINE_ONLY_MEETING_LINK_UNAVAILABLE",
                            message="Meeting link generation unavailable during ONLINE_ONLY confirmation",
                            request_id=request_id,
                            trace_id=trace_id,
                            context={
                                "provider": (
                                    configured_active_provider.value
                                    if configured_active_provider
                                    else active_provider.value
                                ),
                                "meeting_link_status": meeting_link_status.value,
                                "error_code": meeting_link_error_code,
                            },
                        ),
                    )
                except Exception as alert_err:
                    logger.warning(
                        "booking.meeting_link.degraded.alert.failed: %s", alert_err
                    )
        elif user_chose_online and mode == MeetingMode.HYBRID:
            # HYBRID mode with ONLINE selection: for Phase 16, no link generation yet (D-09)
            # Just snapshot the meeting type as ONLINE but status NOT_REQUIRED
            meeting_type_snapshot = "ONLINE"
            meeting_link_status = MeetingLinkStatus.NOT_REQUIRED
        else:
            # OFFLINE or HYBRID with OFFLINE selection
            meeting_type_snapshot = "OFFLINE"
            meeting_link_status = MeetingLinkStatus.NOT_REQUIRED

        booking = await attempt_booking(
            db=db,
            session_id=chat_session.session_id,
            org_id=str(org.id),
            slot_id=str(target_slot.id),
            customer_name=chat_session.collected_data.get("name"),
            customer_email=customer_email,
            customer_phone=chat_session.collected_data.get("phone"),
            user_timezone=effective_user_tz,
            user_slot_start_at=user_slot_start_at,
            user_slot_end_at=user_slot_end_at,
            timezone_source=user_timezone_source,
            org_timezone_snapshot=org_timezone_snapshot,
            org_slot_start_at=org_slot_start_at,
            org_slot_end_at=org_slot_end_at,
            # Meeting link fields (Phase 16-02)
            meeting_type_snapshot=meeting_type_snapshot,
            meeting_link_url=meeting_link_url,
            meeting_link_provider=(
                meeting_link_provider.value if meeting_link_provider else None
            ),
            meeting_link_status=meeting_link_status.value
            if meeting_link_status
            else None,
            meeting_link_external_id=meeting_link_external_id,
            meeting_link_error_code=meeting_link_error_code,
        )

        if background_tasks:
            log_audit_event(
                background_tasks,
                org_id=org.id,
                actor_id=None,
                actor_email=customer_email,
                action=AuditAction.BOOKING_CREATE,
                resource_type="booking",
                resource_id=str(booking.id),
                old_values=None,
                new_values={
                    "session_id": chat_session.session_id,
                    "slot_id": str(target_slot.id),
                    "customer_name": chat_session.collected_data.get("name"),
                    "customer_email": customer_email,
                    "customer_phone": chat_session.collected_data.get("phone"),
                    "date": chat_session.collected_data.get("date"),
                    "time": chat_session.collected_data.get("time"),
                    "user_timezone": effective_user_tz,
                    "timezone_source": user_timezone_source,
                    "status": "CONFIRMED",
                },
                ip_address=None,
                user_agent=None,
            )

        chat_session.session_status = "CONFIRMED"
        _clear_lock_slot_metadata(chat_session)
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

        from app.core.org_notify import notify_org

        await notify_org(
            org_id=org.id,
            kind="booking_created",
            title="New appointment booked",
            body=(
                f"{chat_session.collected_data.get('name') or 'A customer'} booked "
                f"{chat_session.collected_data.get('date') or 'a slot'} at "
                f"{chat_session.collected_data.get('time') or ''}".strip()
                + "."
            ),
            resource_type="booking",
            resource_id=str(booking.id),
        )

        # Create Google Calendar event (best-effort)
        config_result = await db.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        org_config = config_result.scalars().first()

        if (
            org_config
            and org_config.google_calendar_connected
            and org_config.google_calendar_refresh_token
        ):
            try:
                from app.core.integrations.google_calendar import (
                    create_booking_calendar_event,
                )
                from datetime import timedelta

                event_start = canonical_start_utc
                event_end = canonical_end_utc

                # Map channel code to display name
                channel_display = {
                    "web": "Web Widget",
                    "whatsapp": "WhatsApp",
                }.get(
                    chat_session.channel, str(chat_session.channel or "Unknown").title()
                )

                google_event_id = await create_booking_calendar_event(
                    org_id=str(org.id),
                    refresh_token=org_config.google_calendar_refresh_token,
                    customer_name=chat_session.collected_data.get("name"),
                    customer_email=customer_email,
                    start_time=event_start,
                    end_time=event_end,
                    booking_id=str(booking.id),
                    customer_phone=chat_session.collected_data.get("phone"),
                    source_channel=channel_display,
                    service_type="Appointment",
                    org_name=org.name or "Booking",
                    display_timezone=org_timezone_snapshot,
                )

                if google_event_id:
                    booking.google_event_id = google_event_id
                    await db.commit()
                    logger.info(
                        f"Google Calendar event {google_event_id} created for booking {booking.id}"
                    )
            except Exception as cal_err:
                logger.error(f"Failed to create Google Calendar event: {cal_err}")

        # Send confirmation emails (best-effort)
        booking_details = dict(chat_session.collected_data)
        booking_details["booking_id"] = str(booking.id)
        booking_details["user_timezone"] = effective_user_tz
        booking_details["timezone_source"] = user_timezone_source
        if fallback_used:
            booking_details["timezone_fallback_used"] = True
            booking_details["timezone_fallback_reason"] = fallback_reason
            booking_details["timezone_notice"] = (
                "We couldn't detect your timezone, so we used the business timezone for this booking."
            )

        # Add source channel to booking details for emails
        booking_details["source_channel"] = {
            "web": "Web Widget",
            "whatsapp": "WhatsApp",
        }.get(chat_session.channel, chat_session.channel.title())

        # Add meeting-link fields from booking (D-06, D-07 from 16-CONTEXT.md)
        if booking.meeting_link_status:
            booking_details["meeting_link_status"] = booking.meeting_link_status.value
        if booking.meeting_link_url:
            booking_details["meeting_link_url"] = booking.meeting_link_url
        if booking.meeting_link_provider:
            booking_details["meeting_link_provider"] = (
                booking.meeting_link_provider.value
            )
        if booking.meeting_type_snapshot:
            booking_details["meeting_type_snapshot"] = booking.meeting_type_snapshot

        # Add dual-time context (D-08 from 16-CONTEXT.md)
        booking_details["org_timezone_snapshot"] = org_timezone_snapshot
        booking_details["user_slot_start_at"] = user_slot_start_at.isoformat()
        booking_details["user_slot_end_at"] = user_slot_end_at.isoformat()
        booking_details["org_slot_start_at"] = org_slot_start_at.isoformat()
        booking_details["org_slot_end_at"] = org_slot_end_at.isoformat()

        from app.models.user import User
        from sqlalchemy.future import select as sa_select

        admin_result = await db.execute(sa_select(User).where(User.org_id == org.id))
        admin_user = admin_result.scalars().first()
        admin_email = admin_user.email if admin_user else None

        email_phrase = ""
        if dispatch_notifications:
            from app.core.mail import send_confirmation_emails

            email_delivery_failed = False
            try:
                if background_tasks:
                    background_tasks.add_task(
                        send_confirmation_emails,
                        customer_email,
                        admin_email,
                        booking_details,
                        org_id=org.id,
                    )
                else:
                    await send_confirmation_emails(
                        customer_email,
                        admin_email,
                        booking_details,
                        org_id=org.id,
                    )
            except Exception as mail_err:
                logger.error(
                    f"Confirmation email failed (booking still confirmed): {mail_err}"
                )
                email_delivery_failed = True

            if email_delivery_failed:
                return (
                    "🎉 Your booking is confirmed! I couldn't send the confirmation email right now, but your reservation is saved.",
                    "BOOKING_CONFIRMED_CARD",
                    None,
                )
            if customer_email:
                if background_tasks:
                    email_phrase = " A confirmation email is being sent now."
                else:
                    email_phrase = " I've sent a confirmation email to you."
        # D-03: Response should mention link unavailability for degraded bookings
        if meeting_link_degraded:
            return (
                "🎉 Your booking is confirmed! The meeting link couldn't be generated right now. "
                "Please contact support or retry provider connection from the dashboard. Your reservation is saved."
                + email_phrase,
                "BOOKING_CONFIRMED_CARD",
                None,
            )
        if fallback_used:
            return (
                "🎉 Your booking is confirmed! We used the business timezone because your timezone couldn't be detected."
                + email_phrase,
                "BOOKING_CONFIRMED_CARD",
                None,
            )
        return (
            "🎉 Your booking is confirmed!"
            + email_phrase
            + " We look forward to seeing you!",
            "BOOKING_CONFIRMED_CARD",
            None,
        )

    except ValueError as slot_err:
        logger.warning(f"Booking slot conflict: {slot_err}")
        chat_session.session_status = "ACTIVE"
        updated_data = {
            k: v for k, v in chat_session.collected_data.items() if k != "time"
        }
        chat_session.collected_data = updated_data
        chat_session.missing_fields = ["time"]
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        return (
            "That slot was just taken by someone else. Could you please choose a different time?",
            "REQUIRE_TIME_SLOT_GRID",
            None,
        )

    except Exception as e:
        logger.error(f"Final booking failed: {e}", exc_info=True)
        chat_session.session_status = "ACTIVE"
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        return (
            "I'm sorry, something went wrong while finalizing your booking. Please try confirming again.",
            "REQUEST_CONFIRMATION",
            None,
        )


async def _lock_slot_for_booking(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
) -> None:
    """
    Reserve the slot the user selected by creating (or re-locking) a TimeSlot row.

    In the virtual slot model, TimeSlot rows are NOT pre-generated. This function
    creates the row at lock time. If booking is abandoned, the sweeper deletes it.
    If booking completes, attempt_booking() sets is_booked=True on this same row.
    """
    from dateutil.parser import parse as date_parse
    from app.engine.slot_engine import create_timeslot_lock
    from app.crud.booking import SLOT_LOCK_MINUTES

    target_date_str = chat_session.collected_data.get("date")
    requested_time = chat_session.collected_data.get("time")

    if not target_date_str or not requested_time:
        return

    try:
        if _is_date_only_value(str(requested_time)):
            return

        parsed_date = date_parse(target_date_str).date()
        req_time = date_parse(requested_time).time()

        slot = await create_timeslot_lock(
            db=db,
            org_id=org.id,
            target_date=parsed_date,
            requested_time=req_time,
            duration_minutes=0,  # 0 = use schedule's slot_duration_minutes
            lock_minutes=SLOT_LOCK_MINUTES,
        )
        if slot:
            lock_start_utc = (
                slot.start_time.replace(tzinfo=timezone.utc)
                if slot.start_time.tzinfo is None
                else slot.start_time.astimezone(timezone.utc)
            )
            chat_session.collected_data["locked_slot_id"] = str(slot.id)
            chat_session.collected_data["locked_slot_start_utc"] = (
                lock_start_utc.isoformat()
            )
            logger.info(
                f"Lock-slot created/refreshed: {slot.id} until {slot.locked_until}"
            )
        else:
            _clear_lock_slot_metadata(chat_session)
            logger.warning(
                f"Could not lock slot for {target_date_str} {requested_time} — already taken"
            )
    except Exception as e:
        _clear_lock_slot_metadata(chat_session)
        logger.warning(f"Failed to lock slot: {e}")
        pass


def _format_display_value(key: str, val: str, fd=None) -> str:
    """Format raw values for human-friendly display in summary cards."""
    field_type = fd.type if fd else key
    try:
        if field_type == "date" or key == "date":
            from dateutil.parser import parse as date_parse

            dt = date_parse(val)
            return dt.strftime("%B %d, %Y")  # "March 25, 2026"
        elif field_type == "time" or key == "time":
            from dateutil.parser import parse as date_parse

            dt = date_parse(val)
            return dt.strftime("%I:%M %p").lstrip("0")  # "3:00 PM"
    except Exception:
        pass
    return val


def _iter_user_facing_summary_fields(
    chat_session: ChatSessionState,
    fd_map: Dict[str, FieldDefinition],
):
    data = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
    for key, fd in fd_map.items():
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        if isinstance(val, (list, dict)) and not val:
            continue
        yield key, val, fd


def _build_summary_card(
    chat_session: ChatSessionState, fd_map: Dict[str, FieldDefinition]
) -> Tuple[str, str]:
    summary_lines = []
    for key, val, fd in _iter_user_facing_summary_fields(chat_session, fd_map):
        icon = fd.icon if fd else "📋"
        label = fd.label if fd else key.replace("_", " ").title()
        display_val = _format_display_value(key, val, fd)
        summary_lines.append(f"{icon} **{label}**: {display_val}")
    if not summary_lines:
        summary_lines.append("📋 **Details**: Please review the details above before confirming.")
    summary_text = (
        "Perfect! Let's double check your details:\n\n"
        + "\n".join(summary_lines)
        + "\n\nShall I go ahead and book this for you?"
    )
    return summary_text, "REQUEST_CONFIRMATION"


def _build_edit_summary(
    chat_session: ChatSessionState, valid_data: dict, fd_map: Dict[str, FieldDefinition]
) -> Tuple[str, str]:
    edited_keys = set(valid_data.keys())
    summary = []
    for key, val, fd in _iter_user_facing_summary_fields(chat_session, fd_map):
        icon = fd.icon if fd else "📋"
        label = fd.label if fd else key.replace("_", " ").title()
        display_val = _format_display_value(key, val, fd)
        mark = " ✏️" if key in edited_keys else ""
        summary.append(f"{icon} **{label}:** {display_val}{mark}")
    if not summary:
        summary.append("📋 **Details:** No editable fields collected yet.")
    return (
        f"Got it, I've updated that for you! Here's your revised summary:\n\n"
        + "\n".join(summary)
        + "\n\nShall I go ahead and book this?",
        "REQUEST_CONFIRMATION",
    )


def _build_confirmation_summary(
    chat_session: ChatSessionState, fd_map: Dict[str, FieldDefinition]
) -> Tuple[str, str]:
    summary = []
    for key, val, fd in _iter_user_facing_summary_fields(chat_session, fd_map):
        icon = fd.icon if fd else "📋"
        label = fd.label if fd else key.replace("_", " ").title()
        display_val = _format_display_value(key, val, fd)
        summary.append(f"{icon} **{label}:** {display_val}")
    if not summary:
        summary.append("📋 **Details:** Please review your booking information.")
    return (
        f"Perfect! I have everything I need. Here's a summary of your appointment:\n\n"
        + "\n".join(summary)
        + "\n\nShall I go ahead and book this?",
        "REQUEST_CONFIRMATION",
    )


async def _save_transcript(
    db: AsyncSession,
    session_id: str,
    user_msg: str,
    agent_msg: str,
    directive: str,
    intent: str = None,
    is_terminal: bool = False,
):
    """Save transcript messages. Best-effort — errors are logged but swallowed."""
    try:
        await crud_chat.save_message(db, session_id, "user", user_msg)
        metadata = {"directive": directive}
        if intent:
            metadata["intent"] = intent
        if is_terminal:
            metadata["is_terminal"] = True
        await crud_chat.save_message(
            db, session_id, "agent", agent_msg, metadata=metadata
        )
    except Exception as e:
        logger.warning(f"Failed to persist transcript: {e}")
