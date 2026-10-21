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
from typing import Any, Dict, List, Optional, Tuple
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from asgi_correlation_id.context import correlation_id
from app.services.payment_service import payment_service

def _is_out_of_scope_request(message: str, business_scope_text: str) -> tuple[bool, str | None]:
    """Pre-LLM guard — disabled in favor of strict LLM prompt BUSINESS SCOPE (dynamic, no hardcoding)."""
    return False, None


def _is_hallucinated_booking_response(user_message: str, llm_response: str, business_scope_text: str) -> bool:
    """Post-LLM guard — defers to LLM prompt's BUSINESS SCOPE (dynamic)."""
    return False

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


async def _payments_ready_for_org(
    db: AsyncSession, org_id, org_config
) -> tuple[bool, str]:
    """(payments_ready, provider) for an org's booking payment rail.

    Cashfree orgs are gated on their own credentials (kill switch + own keys
    or platform fallback). Razorpay orgs stay gated on Razorpay Route
    TRANSFER_READY. Fail closed either way.
    """
    provider = (getattr(org_config, "payment_provider", "razorpay") or "razorpay").lower()
    try:
        if provider == "cashfree":
            from app.services.cashfree.org_creds import is_cashfree_usable

            return bool(await is_cashfree_usable(db, org_id)), provider
        from app.services.razorpay.routing import get_org_route_context

        return bool((await get_org_route_context(db, org_id)).transfer_ready), provider
    except Exception:
        return False, provider


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
    "recend",
    "recend otp",
    "resent",
    "resent otp",
    "send again",
    "new code",
    "didn't get",
    "didnt get",
    "didn't receive",
    "didnt receive",
    "didn't received",
    "didnt received",
    "did not receive",
    "did not received",
    "haven't received",
    "havent received",
    "hasn't received",
    "hasnt received",
    "not received",
    "not got",
    "never received",
    "never got",
    "no otp",
    "no code",
    "waiting for otp",
    "waiting for code",
    "where is otp",
    "where is code",
    "otp not received",
    "code not received",
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


# Intents where the LLM did NOT make a decision (no decisive classification).
# Keyword/regex lists below are FALLBACK ONLY — they may override the intent
# solely when the LLM verdict is one of these (or the LLM was bypassed/down).
# Decisive LLM intents (CONFIRM_BOOKING, EDIT_DATA, CANCEL_BOOKING,
# CHECK_BOOKING) are never stomped by substring matching.
_LLM_INDECISIVE_INTENTS = frozenset({"PROVIDE_DATA", "START_BOOKING", "ASK_QUESTION"})


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


def _build_phone_otp_context_key(
    chat_session: ChatSessionState,
) -> str:
    if not isinstance(chat_session.collected_data, dict):
        return f"otp_phone:{chat_session.session_id}:::"
    raw_phone = str(chat_session.collected_data.get("phone") or "").strip()
    # Normalize to E.164-like digits to keep "63500 63338" == "6350063338" == "+916350063338"
    try:
        import phonenumbers

        pn = phonenumbers.parse(raw_phone, "IN" if len(raw_phone.replace(" ", "").replace("-", "").lstrip("+")) <= 11 else None)
        phone = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164) if phonenumbers.is_possible_number(pn) else re.sub(r"\D", "", raw_phone)
    except Exception:
        phone = re.sub(r"\D", "", raw_phone)
    date_value = _normalize_otp_date_component(chat_session.collected_data.get("date"))
    time_value = _normalize_otp_time_component(chat_session.collected_data.get("time"))
    return f"otp_phone:{chat_session.session_id}:{phone}:{date_value}:{time_value}"


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
        r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
        raw,
    ):
        return True
    return False


_GREETING_ONLY_RE = re.compile(
    r"^(hi+|hello+|hey+|yo|sup|namaste|namaskar|salaam+|hola|bonjour|good\s*(morning|afternoon|evening|day))[\s.!…?,]*$",
    re.IGNORECASE,
)


def _is_greeting_only(text: str) -> bool:
    """True when the message is just a greeting (punctuation tolerated).

    Guards the LLM-extracted `name` slot: greetings like "Hii.." carry no
    identity and must never become the customer's name.
    """
    return bool(_GREETING_ONLY_RE.match((text or "").strip()))


# A greeting that LEADS a name ("Hi Yash", "Hello Amanpreet Kaur"). Stripped
# before deciding whether the rest of the message is a name.
_GREETING_PREFIX_RE = re.compile(
    r"^(?:hi+|hello+|hey+|yo|sup|namaste|namaskar|salaam+|hola|bonjour|"
    r"good\s*(?:morning|afternoon|evening|day))\b[\s,.!…?]*",
    re.IGNORECASE,
)
_VOWELS = frozenset("aeiou")
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")

# Function words, question words, greetings and address words. A person's
# name essentially never contains one, so their presence means the customer
# sent a sentence ("can you help"), not a name. The asymmetry is deliberate:
# guessing wrong only costs the customer one more question, whereas adopting
# a sentence as a name would greet them with nonsense and derail the booking.
_NOT_A_NAME_WORD = frozenset(
    """
    a an the this that these those there here
    i im me my mine myself we us our you your yours yourself he him his
    she her hers they them their theirs
    is am are was were be been being
    and or but if then than so because
    to of for from with on in at by as about into over under again
    do does did done can could shall should must
    what whats what's when where which who whom whose why how hows how's
    please thanks thank hi hello hey yo sup ok okay yes no yeah yup nope sure
    help want need like get got give tell know think have has had
    book booking appointment appointments available availability slot slots
    schedule time date day days morning afternoon evening night today tomorrow
    now also just very really much many good
    doctor dr sir madam maam team office support admin everyone all folks guys
    """.split()
)


def _looks_like_keyboard_mash(word: str) -> bool:
    """True for runs of adjacent keys ("qwertyuiop", "asdfgh")."""
    lowered = word.lower()
    if len(lowered) < 4:
        return False
    return any(row in lowered or lowered in row for row in _KEYBOARD_ROWS)


def extract_opening_name(message_text: str) -> str:
    """Pull a person's name out of a customer's OPENING message.

        "Hi Yash"          -> "Yash"
        "Amanpreet Kaur"   -> "Amanpreet Kaur"
        "Hello! Amanpreet" -> "Amanpreet"

    Returns "" for anything that is not a bare name, so a question, an email,
    a phone number, a date, a greeting on its own, or keysmash garbage is
    never adopted as the customer's identity. Deliberately conservative: a
    false positive would greet someone by a nonsense word, and a false
    negative only means the customer is asked for their name as before.
    Self-introductions ("I am Rohan Verma") are the MODEL's job to parse —
    no keyword stripping here.
    """
    raw = " ".join(str(message_text or "").split())
    if not raw:
        return ""
    without_greeting = _GREETING_PREFIX_RE.sub("", raw).strip(" .!,…?")
    if not without_greeting:
        return ""
    # Anything structured is a different field, not a name.
    if any(ch.isdigit() for ch in without_greeting):
        return ""
    if any(ch in without_greeting for ch in "@/\\.:;()[]{}\"'&+*=~`$%^<>|"):
        return ""
    if "?" in without_greeting:
        return ""
    words = without_greeting.split()
    if not words or len(words) > 3:
        return ""
    for word in words:
        if word.lower() in _NOT_A_NAME_WORD:
            return ""
        # No vowel at all => not a name ("Ghhjjkk").
        if not any(ch.lower() in _VOWELS for ch in word):
            return ""
        # 3+ identical letters in a row => keysmash, not a name.
        if re.search(r"(.)\1{2,}", word):
            return ""
        # A repeated syllable => keysmash ("Blaahahhahah" repeats "hah",
        # "Nahahhahahahz" repeats it three times). Production: a customer
        # typed Blaahahhahah and it became the name on a real booking. A
        # 3-char unit twice in a row is already vanishingly rare in real
        # names; a 2-char unit needs three in a row ("aa" in "Amanpreet" and
        # "mm" in "Mohammed" must keep passing).
        lowered_word = word.lower()
        if re.search(r"(.{3})\1", lowered_word):
            return ""
        if re.search(r"(.{2})\1{2}", lowered_word):
            return ""
        # 4+ consonants in a row is not a name ("Babbzhahavq", "Gayhajvndkso").
        if re.search(r"[^aeiou]{4,}", lowered_word):
            return ""
        if _looks_like_keyboard_mash(word):
            return ""
        if len(word) > 24:
            return ""
    if _is_greeting_only(without_greeting) or _is_reserved_name_value(without_greeting):
        return ""
    if not _looks_like_name_candidate(without_greeting):
        return ""
    return without_greeting


# Words that can never be a person's name — the model sometimes echoes the
# field/topic word itself ("Email ka code..." → name="Email"). Matched
# case-insensitively against the whole normalized value.
_RESERVED_NAME_VALUES = frozenset(
    {
        "email",
        "e-mail",
        "mail",
        "phone",
        "mobile",
        "number",
        "date",
        "time",
        "day",
        "otp",
        "code",
        "booking",
        "appointment",
        "slot",
        "name",
        "verification",
    }
)


def _is_reserved_name_value(value: object) -> bool:
    text = re.sub(r"[^a-z]+", "", str(value or "").lower())
    return text in _RESERVED_NAME_VALUES


_COLLECTION_INTENTS = ("START_BOOKING", "PROVIDE_DATA")


def _field_request_cues(field_name: Optional[str], field_label: Optional[str]) -> set:
    """Keyword cues proving a reply asks for a given field."""
    name = (field_name or "").strip().lower()
    cues = {name.replace("_", " "), name} if name else set()
    if field_label:
        cues |= {
            word
            for word in re.split(r"[^a-z]+", field_label.lower())
            if len(word) > 2
        }
    if name == "email":
        cues |= {"e-mail", "mail", "verification code"}
    elif name == "phone":
        cues |= {"mobile", "number", "contact", "whatsapp"}
    elif name == "date":
        cues |= {"day", "when"}
    elif name == "time":
        cues |= {"slot", "timing"}
    elif name == "name":
        cues |= {"called", "call you", "your name"}
    return {cue for cue in cues if cue}


def _response_asks_for_field(
    response: str, field_name: Optional[str], field_label: Optional[str]
) -> bool:
    """True when a free-form reply visibly asks for the given field."""
    text = (response or "").lower()
    if not text or not field_name:
        return False
    return any(cue in text for cue in _field_request_cues(field_name, field_label))


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
            # Strict shared extractor (greetings stripped, sentences and
            # keysmash rejected). The old code validated the RAW message, so
            # "I am Rohan Verma" passed as a name and was BOOKED verbatim.
            # Self-introductions are the model's job to parse; here a
            # rejection only re-asks — safe.
            candidate = extract_opening_name(message_text)
            if not candidate:
                continue
            parsed_name, err = DataValidator._validate_name(candidate)
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


def _upcoming_date_options(
    org_timezone: str, max_booking_days: int, count: int = 7
) -> list:
    """Next bookable day labels for WhatsApp list rows.

    Titles stay plain ("Sat, Sep 13") so both taps and typed text parse
    through the normal date validator. Shows next `count` bookable days
    within horizon — caller can filter further via slot_engine if needed.
    """
    try:
        tz = ZoneInfo(str(org_timezone or "UTC"))
    except Exception:
        tz = timezone.utc
    today = datetime.now(tz).date()
    days = []
    for offset in range(int(max_booking_days or 90) + 1):
        if len(days) >= count:
            break
        day = today + timedelta(days=offset)
        if (day - today).days > int(max_booking_days or 90):
            break
        # Include day — availability (slots/closed) is enforced downstream
        # but we already honour horizon; keeping this broad avoids empty lists
        # when callers want a quick prompt. Use filtered helper for strict view.
        days.append(day.strftime("%a, %b %d"))
    return days


_WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


def format_closed_days_note(closed_weekdays) -> str:
    """Human sentence naming the days the business does not open.

    Reads the admin's own schedule (ScheduleDay.is_active), so the message can
    never drift from the hours the admin configured. Returns "" when the
    business is open every day, so nothing is claimed that is not true.
    """
    try:
        # Out-of-range values are dropped rather than wrapped with %7, so bad
        # data can never turn into a confident claim that the shop is shut.
        days = sorted({int(d) for d in (closed_weekdays or []) if 0 <= int(d) <= 6})
    except (TypeError, ValueError):
        return ""
    if not days:
        return ""
    names = [_WEEKDAY_NAMES[d] for d in days if 0 <= d <= 6]
    if not names:
        return ""
    if len(names) == 1:
        return f"We are closed on {names[0]}."
    if len(names) == 2:
        return f"We are closed on {names[0]} and {names[1]}."
    return "We are closed on " + ", ".join(names[:-1]) + f" and {names[-1]}."


async def fetch_closed_weekdays(db, org_id) -> list:
    """Weekday indices (0=Mon) the business is closed, per the admin schedule.

    A weekday counts as closed when it is either explicitly inactive or has no
    row at all. If the admin has not configured any schedule, nothing is
    reported rather than guessing that the business is shut.
    """
    try:
        from sqlalchemy.future import select as sa_select

        from app.models.booking import ScheduleDay

        rows = (
            await db.execute(
                sa_select(ScheduleDay.day_of_week, ScheduleDay.is_active).where(
                    ScheduleDay.org_id == org_id,
                    ScheduleDay.provider_id.is_(None),
                )
            )
        ).all()
        if not rows:
            return []
        return [
            int(day)
            for day, is_active in rows
            if day is not None and not bool(is_active)
        ]
    except Exception:
        logger.warning("Failed to read closed weekdays", exc_info=True)
        return []


def _offered_slot_labels(chat_session, field_meta=None) -> list:
    """The slot labels the customer was last shown, in order.

    Read from the session (the list we actually rendered) with the directive
    meta as a fallback. When nothing was offered we return an empty list, and
    the caller must then skip the check rather than reject every time.
    """
    labels = []
    if isinstance(field_meta, dict):
        for key in ("available_slots", "options"):
            raw = field_meta.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        value = item.get("label") or item.get("value")
                        if value:
                            labels.append(str(value))
                    elif isinstance(item, str) and item.strip():
                        labels.append(item)
            if labels:
                break
    if labels:
        return labels
    try:
        stored = (chat_session.collected_data or {}).get("offered_time_slots")
    except Exception:
        stored = None
    if isinstance(stored, list) and stored:
        return [str(x) for x in stored if str(x).strip()]
    return []


def _format_available_dates_for_prompt(
    org_timezone: str,
    max_booking_days: int,
    count: int = 7,
    closed_weekdays=None,
) -> str:
    """Horizon-based date prompt as requested: 'We are available from 15 September to 15 November...'"""
    try:
        tz = ZoneInfo(str(org_timezone or "UTC"))
    except Exception:
        tz = timezone.utc
    today = datetime.now(tz).date()
    end = today + timedelta(days=int(max_booking_days or 30))
    # Format like '15 September' without year, title-case — but include the
    # year whenever the window crosses into another year (or spans one),
    # otherwise "21 September to 26 September" reads as 5 days for a 370-day
    # window and Nov→Jan windows are ambiguous.
    start_str = today.strftime("%d %B").lstrip("0")
    end_str = end.strftime("%d %B").lstrip("0")
    if end.year != today.year:
        start_str = f"{start_str} {today.year}"
        end_str = f"{end_str} {end.year}"
    horizon = f"We are available from {start_str} to {end_str}, please choose a date within this horizon."
    # Tell the customer up front which days are shut, so they don't pick one
    # and get bounced back. Driven by the admin's configured hours.
    closed_note = format_closed_days_note(closed_weekdays)
    if closed_note:
        horizon += f" {closed_note}"
    if (org_timezone or "").strip():
        horizon += f" (All dates in {org_timezone.strip()}.)"
    return horizon


def _format_slot_duration_human(minutes: int | None) -> str:
    if not minutes or minutes <= 0:
        return ""
    if minutes == 60:
        return "1 hr"
    if minutes % 60 == 0:
        hrs = minutes // 60
        return f"{hrs} hr" if hrs == 1 else f"{hrs} hrs"
    if minutes < 60:
        return f"{minutes} minutes"
    hrs = minutes // 60
    mins = minutes % 60
    return f"{hrs} hr {mins} minutes" if hrs == 1 else f"{hrs} hrs {mins} minutes"


def _format_time_prompt_for_whatsapp(
    target_date_str: str,
    timezone_name: str,
    slot_minutes: int | None,
    weekly_schedule: list[dict] | None = None,
    exceptions: list[dict] | None = None,
) -> str:
    """Time prompt: 'Our available booking hours are from 9:30 to 6:30 and your booking will be of 1 hr, please choose your time for booking.'"""
    # Resolve hours for this specific date
    try:
        from dateutil.parser import parse as _parse
        target = _parse(str(target_date_str)).date()
    except Exception:
        target = None
    hours_text = ""
    if target and weekly_schedule is not None:
        # check exception first
        is_closed = False
        custom_open = None
        custom_close = None
        if exceptions:
            for ex in exceptions:
                ex_date = ex.get("exception_date")
                if isinstance(ex_date, str):
                    try:
                        ex_date = __import__("datetime").date.fromisoformat(ex_date)
                    except Exception:
                        continue
                if ex_date == target:
                    if str(ex.get("exception_type") or "").upper() == "CLOSED":
                        is_closed = True
                    else:
                        custom_open = ex.get("custom_open")
                        custom_close = ex.get("custom_close")
                    break
        if is_closed:
            return f"We are closed on {target.strftime('%A, %d %B').lstrip('0')} — please choose another date."
        # weekly schedule for this weekday
        if custom_open and custom_close:
            # custom hours
            try:
                from app.engine.scheduling_policy import _coerce_time as _ct, _format_display_time as _ft
                o = _ct(custom_open); c = _ct(custom_close)
                if o and c:
                    hours_text = f"{_ft(o)} to {_ft(c)}"
            except Exception:
                pass
        else:
            # normal weekday
            for item in weekly_schedule or []:
                try:
                    if int(item.get("day_of_week", -1)) == target.weekday():
                        if not item.get("is_active"):
                            return f"We are closed on {target.strftime('%A, %d %B').lstrip('0')} — please choose another date."
                        from app.engine.scheduling_policy import _coerce_time as _ct, _format_display_time as _ft
                        o = _ct(item.get("open_time")); c = _ct(item.get("close_time"))
                        if o and c:
                            hours_text = f"{_ft(o)} to {_ft(c)}"
                        break
                except Exception:
                    continue
    # Fallback if no schedule
    if not hours_text and weekly_schedule:
        # try generic first active
        try:
            from app.engine.scheduling_policy import _coerce_time as _ct, _format_display_time as _ft
            for item in weekly_schedule:
                if item.get("is_active"):
                    o = _ct(item.get("open_time")); c = _ct(item.get("close_time"))
                    if o and c:
                        hours_text = f"{_ft(o)} to {_ft(c)}"
                        break
        except Exception:
            pass
    if not hours_text:
        return "We don't have published hours for that day yet. Please choose another date or contact us."
    dur = _format_slot_duration_human(slot_minutes)
    dur_part = f" and your booking will be of {dur}" if dur else ""
    tz_note = f" (All times in {timezone_name})." if (timezone_name or "").strip() else ""
    return f"Our available booking hours are from {hours_text}{dur_part}, please choose your time for booking.{tz_note}"


def _format_business_hours_for_whatsapp(
    weekly_schedule: list[dict],
    timezone_name: str,
    slot_minutes: int | None = None,
) -> str:
    """Short, WhatsApp-friendly business hours line (no horizon)."""
    from app.engine.scheduling_policy import format_weekly_hours_summary

    summary = format_weekly_hours_summary(
        weekly_schedule, timezone_name, slot_minutes=slot_minutes, booking_window_days=None
    )
    if not summary:
        return ""
    # summary example: "Monday to Sunday: 9:00 AM – 5:00 PM (times in Asia/Calcutta). each appointment is 120 minutes long."
    # Clean up: capitalize, keep timezone only once
    # Remove trailing horizon part if any, keep concise
    return summary.rstrip(".")


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
    # Honesty marker first: the model is down and regex fallback is driving,
    # so the customer is told plainly. "didn't quite catch" is kept because
    # off-topic accounting keys off it (a recovery ask must still count as a
    # decline-shaped turn) and the orchestrator's fallback-recovery detector
    # keys off "temporary issue" (a regex-collected time must survive the
    # slot-button guard instead of being popped as unselected).
    lead = (
        "I'm having a temporary issue, so I didn't quite catch that one — "
        "but no problem, we can keep going."
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


def _format_confirmed_when(collected_data) -> str:
    try:
        from dateutil.parser import parse as date_parse

        data = collected_data if isinstance(collected_data, dict) else {}
        date_raw = str(data.get("date") or "").strip()
        time_raw = str(data.get("time") or "").strip()
        if not date_raw or not time_raw:
            return ""
        parsed_date = date_parse(date_raw).date()
        when_date = f"{parsed_date.strftime('%B')} {parsed_date.day}"
        parsed_time = date_parse(time_raw).time()
        hour = parsed_time.hour % 12 or 12
        suffix = "AM" if parsed_time.hour < 12 else "PM"
        if parsed_time.minute:
            when_time = f"{hour}:{parsed_time.minute:02d} {suffix}"
        else:
            when_time = f"{hour} {suffix}"
        return f"{when_date} at {when_time}"
    except Exception:
        return ""


def build_finished_session_reply(chat_session, status: str) -> str:
    status = str(status or "").upper()
    if status == "CONFIRMED":
        data = getattr(chat_session, "collected_data", None)
        when = _format_confirmed_when(data if isinstance(data, dict) else {})
        if when:
            return f"Your booking is already confirmed for {when}. Let us know if you need anything else."
        return "Your booking is confirmed! You can say 'cancel my booking' or 'reschedule' anytime."
    if status == "CANCELLED":
        return "Your previous booking was cancelled. Tap 'Book a new appointment' below to start a new one!"
    if status == "EXPIRED":
        return "This session expired. Tap 'Book a new appointment' below to start a new booking!"
    return "Is there anything else I can help you with?"


# ── Rebook button (cancelled/expired sessions) ──
# After a booking is cancelled (or the session expires) the widget must NOT
# show a free-text "Your answer" card — just one button that restarts the
# booking flow from the beginning.
REBOOK_BUTTON_DIRECTIVE = "SHOW_REBOOK_BUTTON"
REBOOK_BUTTON_LABEL = "Book a new appointment"
REBOOK_BUTTON_PAYLOAD = "Book a new appointment"


def rebook_button_field_meta() -> Dict[str, Any]:
    return {"label": REBOOK_BUTTON_LABEL, "payload": REBOOK_BUTTON_PAYLOAD}


def is_rebook_request_text(message_text: str) -> bool:
    """True when the message asks to start a new booking (incl. the rebook
    button payload). Keyword-based so it never depends on LLM classification."""
    low = str(message_text or "").lower().strip()
    if not low:
        return False
    if low == REBOOK_BUTTON_PAYLOAD.lower():
        return True
    return any(kw in low for kw in REBOOK_KEYWORDS)


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
                selected_iso = parsed.date().isoformat()
            except Exception:
                date_str = date_val
                selected_iso = ""
            # Echo the stored selection first (dynamic, from collected data —
            # never static text), so the customer sees exactly what the
            # booking holds before picking a time.
            echo = f"You have selected {selected_iso} for booking. " if selected_iso else ""
            if user_name:
                return f"Thank you, {user_name.split()[0]}. {echo}Now, please choose your preferred time slot for {date_str} from the options below.{timezone_note}"
            return f"Thank you for providing the date. {echo}Now, please choose your preferred time slot for {date_str} from the options below.{timezone_note}"
        return f"Please choose your preferred time from the available slots below.{timezone_note}"

    if directive == "REQUIRE_DATE_PICKER":
        if user_name:
            return f"Thank you, {user_name.split()[0]}. Please choose your preferred date for the appointment."
        return "Please choose your preferred date for the appointment."

    if directive == "REQUIRE_EMAIL_INPUT":
        return ""

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
            if request_phrase == "your full name":
                return ""
            if user_name:
                return (
                    f"Thank you, {user_name.split()[0]}. Please share {request_phrase}."
                )
            return f"Please share {request_phrase}."
        if field_name:
            request_phrase = _label_to_request(
                field_name.replace("_", " ").title(), "share"
            )
            if request_phrase == "your full name":
                return ""
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
    transcript_user_text: Optional[str] = None,
    inbound_message_id: Optional[str] = None,
) -> ActionPayload:
    """
    Process a single user message through the booking pipeline.
    Returns an ActionPayload with the agent's response + directive.

    This function is CHANNEL-AGNOSTIC — it doesn't know or care whether
    the message came from the web widget, WhatsApp, or SMS.

    transcript_user_text: original user text for transcript rows when callers
    rewrote message_text (e.g. directive-tap option values). Logic still uses
    message_text. inbound_message_id is accepted for forward compatibility.
    """
    # Empty input carries no intent: stay silent (no reply, no transcript,
    # no state change). Inbounds translate SILENT_NO_REPLY to reply=None.
    if not str(message_text or "").strip():
        return ActionPayload(message="", widget_directive="SILENT_NO_REPLY",
                             field_meta={"silent": True})
    _transcript_user_text = (
        transcript_user_text if transcript_user_text is not None else message_text
    )

    # Org dept flow: resolve a post-date team-member choice (same as graph path).
    try:
        from app.services.catalog_service import resolve_pending_provider as _rpp_legacy

        _picked_legacy = await _rpp_legacy(db, org.id, chat_session, message_text)
        if _picked_legacy is not None:
            try:
                await crud_chat.update_chat_session(
                    db, chat_session.session_id, chat_session
                )
            except Exception:
                pass
    except Exception:
        pass

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
            db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
        )
        return action

    if chat_session.session_status in ("CONFIRMED", "CANCELLED", "EXPIRED"):
        return await _handle_post_booking(
            db, org, org_config, chat_session, message_text, background_tasks,
            transcript_user_text=_transcript_user_text,
        )

    # ── Load config values ────────────────────────────────────
    persona = (
        org_config.custom_prompts if isinstance(org_config.custom_prompts, dict) else {}
    )
    # Inject the admin-collected business profile into a shallow copy of the
    # persona so the legacy pipeline is grounded the same way as the LangGraph
    # runtime (never mutate org_config.custom_prompts in place).
    _knowledge_ctx = getattr(org_config, "knowledge_context", None)
    if isinstance(_knowledge_ctx, dict):
        from app.engine.knowledge_profile import format_business_profile
        _biz_block = format_business_profile(_knowledge_ctx)
        if _biz_block:
            persona = dict(persona)
            persona["custom_instructions"] = (
                str(persona.get("custom_instructions") or "").rstrip()
                + ("\n" if persona.get("custom_instructions") else "")
                + _biz_block.replace("## ABOUT THE BUSINESS", "ABOUT THE BUSINESS")
            ).strip()
    # Ground the legacy pipeline with the org's weekly business hours the same
    # way as the LangGraph runtime so basic timing questions are answerable.
    from app.engine.scheduling_policy import load_weekly_hours_block

    _hours_block = await load_weekly_hours_block(db, str(org.id) if org else "")
    if _hours_block:
        persona = dict(persona)
        persona["custom_instructions"] = (
            str(persona.get("custom_instructions") or "").rstrip()
            + ("\n" if persona.get("custom_instructions") else "")
            + _hours_block.replace("## BUSINESS HOURS", "BUSINESS HOURS")
        ).strip()
    # Org-mode: ground the agent in the picked department + doctor so it
    # never hallucinates the wrong doctor and asks for dept first when missing.
    try:
        _unit_name = (chat_session.collected_data or {}).get("unit_name")
        _prov_name = (chat_session.collected_data or {}).get("provider_name")
        if not _unit_name and getattr(chat_session, "unit_id", None):
            from app.models.service_unit import ServiceUnit as _SU

            _u = (
                await db.execute(select(_SU).where(_SU.id == chat_session.unit_id))
            ).scalars().first()
            if _u is not None:
                _unit_name = _u.name
        if not _prov_name and getattr(chat_session, "provider_id", None):
            from app.models.service_unit import Provider as _PV

            _p = (
                await db.execute(select(_PV).where(_PV.id == chat_session.provider_id))
            ).scalars().first()
            if _p is not None:
                _prov_name = _p.full_name
        if _unit_name or _prov_name:
            _org_ctx = "BOOKING CONTEXT (authoritative): "
            if _unit_name:
                _org_ctx += f"department={_unit_name}. "
            if _prov_name:
                _role = ""
                try:
                    _role = str(getattr(_p, "role_label", None) or "").strip()
                except Exception:
                    _role = ""
                _org_ctx += f"team_member={_prov_name}" + (f" ({_role}). " if _role else ". ")
            _org_ctx += "Use only this team member's hours and slots."
            persona = dict(persona)
            persona["custom_instructions"] = (
                str(persona.get("custom_instructions") or "").rstrip()
                + "\n"
                + _org_ctx
            ).strip()
    except Exception:
        pass
    org_timezone = str(org_config.timezone) if org_config.timezone else "UTC"
    # NULL flag = schema default (email ON, phone OFF).
    _req_e = getattr(org_config, "require_otp", True)
    _req_p = getattr(org_config, "require_phone_otp", False)
    require_otp = True if _req_e is None else bool(_req_e)
    require_phone_otp = False if _req_p is None else bool(_req_p)
    from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon_days

    max_booking_days = (await _resolve_horizon_days(db, org.id if org else None, org_config))[0]

    field_definitions = parse_field_definitions(
        org_config.field_definitions or org_config.required_fields
    )
    fd_map = {fd.name: fd for fd in field_definitions}

    # ── Hours question mid-flow — answer deterministically even while booking ─
    # If the user asks about hours/timings while mid-booking, answer from
    # the authoritative schedule instead of letting the LLM re-ask the field.
    # This keeps the message flow natural: hours answer + re-ask current field.
    from app.engine.scheduling_policy import (
        is_schedule_business_hours_query,
        lookup_schedule_business_hours_answer,
        resolve_effective_timezone_policy as _resolve_tz,
    )
    _is_hours_q = False
    try:
        _is_hours_q = is_schedule_business_hours_query(message_text)
    except Exception:
        _is_hours_q = False
    _hours_answer = None
    if _is_hours_q:
        try:
            _tz_for_hours = _resolve_tz(
                requested_tz=str(chat_session.collected_data.get("user_timezone") or "") or None,
                org_tz=str(org_config.timezone or "UTC"),
                requested_source="user_selected",
            )
            _hours_answer = await lookup_schedule_business_hours_answer(
                db=db,
                org_id=str(org.id) if org else "",
                user_message=message_text,
                effective_timezone=str(_tz_for_hours.get("effective_timezone") or org_timezone),
                org_timezone=str(org_config.timezone or "UTC"),
                in_booking_flow=bool(chat_session.collected_data),
            )
        except Exception:
            _hours_answer = None

    # ── Deterministic scope guard — before LLM to prevent hallucination ────
    # Build business scope dynamically from actual configured context, no hardcoded lists
    _scope_parts = []
    if persona.get("industry_context"):
        _scope_parts.append(str(persona.get("industry_context")))
    if persona.get("custom_instructions"):
        _scope_parts.append(str(persona.get("custom_instructions")))
    business_scope_text = " ".join(_scope_parts)
    is_oos, matched_prof = _is_out_of_scope_request(message_text, business_scope_text)
    # ── LLM Orchestration (Intent + Extraction + Response) ────
    prior_intent = chat_session.current_intent
    previous_missing_fields = list(chat_session.missing_fields or [])
    # Hours question mid-booking: answer deterministically and re-ask current field
    if _hours_answer and isinstance(_hours_answer, dict) and _hours_answer.get("response_text"):
        hours_text = str(_hours_answer.get("response_text") or "").strip()
        # Determine what to ask next (current missing field) to keep flow
        _next_for_hours = StateEvaluator.determine_next_action(
            chat_session.missing_fields or [], chat_session.current_intent, field_definitions
        )
        _dir_for_hours = str(_next_for_hours.get("directive") or "TEXT_INPUT")
        _meta_for_hours = _next_for_hours.get("field_meta")
        # If the next ask is date/time, ensure options are populated for WhatsApp lists
        if _dir_for_hours == "REQUIRE_DATE_PICKER" and isinstance(_meta_for_hours, dict) and not _meta_for_hours.get("options"):
            _meta_for_hours["options"] = _upcoming_date_options(org_timezone, max_booking_days)
        # For time, the slot options will be populated later in the post-processing block,
        # but we need to set the directive so the WhatsApp list renders.
        _combined_msg = f"{hours_text}\n\n" + _build_context_aware_response(
            directive=_dir_for_hours,
            field_meta=_meta_for_hours,
            collected_data=chat_session.collected_data,
            valid_data={},
            errors={},
            llm_generated_msg=hours_text,
            agent_name=persona.get("agent_name", "Booking Assistant"),
        )
        # Persist and return directly — no LLM needed, and booking progress is preserved
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        _action = ActionPayload(message=_combined_msg, widget_directive=_dir_for_hours, field_meta=_meta_for_hours)
        await _save_transcript(db, chat_session.session_id, _transcript_user_text, _combined_msg, _dir_for_hours)
        return _action
    # Available-dates inquiry mid-booking ("which dates can I book?"): answer
    # from the live slot inventory, then re-ask the current field so the flow
    # continues. Without this the question falls through to field collection
    # and the user is interrogated instead of answered.
    from app.engine.scheduling_policy import (
        lookup_available_dates_answer as _lookup_dates_answer,
    )
    _dates_answer = None
    try:
        _dates_answer = await _lookup_dates_answer(
            db=db,
            org_id=str(org.id) if org else "",
            user_message=message_text,
            effective_timezone=str(
                chat_session.collected_data.get("user_timezone") or org_timezone
            ),
            org_timezone=org_timezone,
        )
    except Exception:
        _dates_answer = None
    if _dates_answer and isinstance(_dates_answer, dict) and _dates_answer.get("response_text"):
        dates_text = str(_dates_answer.get("response_text") or "").strip()
        _next_for_dates = StateEvaluator.determine_next_action(
            chat_session.missing_fields or [], chat_session.current_intent, field_definitions
        )
        _dir_for_dates = str(_next_for_dates.get("directive") or "TEXT_INPUT")
        _meta_for_dates = _next_for_dates.get("field_meta")
        _combined_dates_msg = f"{dates_text}\n\n" + _build_context_aware_response(
            directive=_dir_for_dates,
            field_meta=_meta_for_dates,
            collected_data=chat_session.collected_data,
            valid_data={},
            errors={},
            llm_generated_msg=dates_text,
            agent_name=persona.get("agent_name", "Booking Assistant"),
        )
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        _dates_action = ActionPayload(message=_combined_dates_msg, widget_directive=_dir_for_dates, field_meta=_meta_for_dates)
        await _save_transcript(db, chat_session.session_id, _transcript_user_text, _combined_dates_msg, _dir_for_dates)
        return _dates_action
    # Field refusal pre-LLM: "no" / "don't want to share X" aimed at the
    # current missing field gets a soft re-ask of the SAME field — the LLM
    # never sees it, so it can neither skip ahead nor interrogate.
    from app.engine import decline_flow as _decline_flow_pre

    _refusal_action = await _decline_flow_pre.maybe_handle_field_refusal(
        db, org, chat_session, message_text, inbound_message_id=inbound_message_id
    )
    if _refusal_action is not None:
        return _refusal_action
    if is_oos:
        scope_label = persona.get("industry_context") or "our services"
        clarifying_msg = (
            f"We only handle bookings for {scope_label} as described in our business profile. "
            f"The service you asked for ('{matched_prof}') is outside our scope. "
            f"Could you please clarify if you'd like to book within our scope? "
            f"For example, you can ask about our available services."
        )
        unified_result = {
            "intent": "ASK_QUESTION",
            "extracted_entities": {},
            "response_message": clarifying_msg,
        }
    else:
        unified_result = await llm_engine.process_turn(
            message_text,
            chat_session.missing_fields,
            chat_session.collected_data,
            chat_session.current_intent,
            persona=persona,
            field_definitions=field_definitions,
            org_id=str(org.id) if org else None,
            db=db,
            business_name=getattr(org_config, "business_name", None),
            org_name=getattr(org, "name", None),
            is_first_turn=is_opening_turn(chat_session),
        )

    intent = unified_result.get("intent", chat_session.current_intent or "PROVIDE_DATA")
    chat_session.current_intent = intent
    raw_entities = unified_result.get("extracted_entities", {})
    llm_generated_msg = unified_result.get(
        "response_message", "I see. Please tell me more."
    )
    # Post-LLM hallucination guard — if LLM affirmed an out-of-scope booking, override
    if _is_hallucinated_booking_response(message_text, llm_generated_msg, business_scope_text):
        scope_label = persona.get("industry_context") or "our services"
        clarifying_msg = (
            f"We only handle bookings for {scope_label} as described in our business profile. "
            f"The service you asked for is outside our scope. "
            f"Could you please clarify if you'd like to book within our scope? "
            f"For example, you can ask about our available services."
        )
        unified_result["response_message"] = clarifying_msg
        unified_result["intent"] = "ASK_QUESTION"
        llm_generated_msg = clarifying_msg
        intent = "ASK_QUESTION"
        chat_session.current_intent = intent
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

    # ── Structural fallback for email/phone when LLM skipped extraction ──
    # Fixes loop: user at email prompt enters same email → LLM thinks "already
    # collected" and returns empty → should_fallback was False because response
    # wasn't short/generic → raw stays empty → bot re-asks email forever.
    # If primary_missing looks like email/phone and message contains structural
    # token, force a direct validator extraction even when LLM was "decisive".
    _early_primary = chat_session.missing_fields[0] if chat_session.missing_fields else None
    if _early_primary and _early_primary not in (raw_entities or {}):
        _direct = None
        if _early_primary == "email" and "@" in message_text:
            try:
                cand, err = DataValidator._validate_email(message_text.strip())
                if cand and not err:
                    _direct = cand
            except Exception:
                pass
        elif _early_primary == "phone" and any(c.isdigit() for c in message_text):
            _fb = _extract_entities_without_llm(
                message_text, [_early_primary], field_definitions, org_timezone, max_booking_days
            )
            if _fb.get(_early_primary):
                _direct = _fb[_early_primary]
        if _direct is not None:
            raw_entities = dict(raw_entities or {})
            raw_entities[_early_primary] = _direct
            llm_temporary_fallback = True
            logger.info(f"structural fallback recovered {_early_primary}: {_direct[:20] if isinstance(_direct, str) else _direct}")

    user_msg_lower = message_text.lower().strip()
    try:
        from app.engine.time_flex import is_confirm_message as _is_confirm_msg

        _msg_is_confirm = _is_confirm_msg(message_text)
    except Exception:
        _msg_is_confirm = any(kw in user_msg_lower for kw in CONFIRM_KEYWORDS)
    # LLM-primary: keyword confirm is fallback only — never stomp a decisive
    # LLM verdict (e.g. "ok, but change the date" → EDIT_DATA, not CONFIRM).
    _llm_undecided = intent in _LLM_INDECISIVE_INTENTS or llm_temporary_fallback
    force_confirm_intent = (
        prior_intent == "CONFIRM_BOOKING"
        and not chat_session.missing_fields
        and _msg_is_confirm
        and _llm_undecided
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

    # ── Decline override (deterministic, pre-LLM classification) ──
    # ARCHITECTURE (LLM driver, code seatbelt): the LLM owns UNDERSTANDING,
    # code owns VERIFICATION. So this gate fires ONLY on single-clause,
    # unambiguous refusals ("no", "not interested") — cheap, exact, instant.
    # Anything longer ("I don't want to book. Can you tell me about X")
    # flows to the LLM, which classifies each half properly; guessing intent
    # with keywords here is exactly how questions got swallowed by farewells.
    # start_decline itself still acknowledges a question half as a safety net
    # (verdict path), but the routing decision for mixed turns belongs to
    # the model, not to regex.
    from app.engine import decline_flow as _decline_flow_gate

    if _decline_flow_gate.is_no_booking_request(
        message_text
    ) and not _decline_flow_gate.carries_knowledge_question(message_text):
        return await _decline_flow_gate.start_decline(
            db, org, chat_session, message_text, inbound_message_id=inbound_message_id
        )
    await _decline_flow_gate.maybe_reengage(db, chat_session, message_text)

    # ── CHECK override (LLM-driven, no keywords) ─────────────
    # When the LLM classifies the turn as CHECK_BOOKING, show live booking
    # details instead of treating the message as a field value. Booking
    # progress is preserved so the user can resume afterwards.
    if intent == "CHECK_BOOKING":
        from app.engine import check_booking_flow as _check_flow

        return await _check_flow.handle_check_booking(
            db, org, org_config, chat_session
        )

    # ── Pending lookup completion ─────────────────────────────
    # Contact sent after a lookup request finishes the check.
    from app.engine import check_booking_flow as _check_flow2

    _pending_action = await _check_flow2.maybe_complete_pending_lookup(
        db, org, org_config, chat_session, message_text
    )
    if _pending_action is not None:
        return _pending_action

    # ── Social-word extraction guard ─────────────────────
    # Greetings/thanks carry no field value: drop any extraction so
    # validation can never save "thanks" as a name. The turn otherwise
    # flows through the normal pipeline (no special routing).
    if intent == "CHITCHAT":
        raw_entities = {}

    # ── CONFIRM override (LLM-primary, but pure CONFIRM always wins) ──
    # "confirm" must always work on the finish screen. Pure confirm tokens
    # force CONFIRM unless the turn is explicitly an edit
    # (e.g. "ok, but change date" → EDIT).
    _has_edit_signal = any(kw in user_msg_lower for kw in ("change", "edit", "modify", "update"))
    _is_edit_mixed = _has_edit_signal and intent == "EDIT_DATA"
    _is_pure_confirm = user_msg_lower.strip() in {
        "confirm", "yes", "yep", "yup", "ok", "okay", "sure", "proceed",
        "book it", "go ahead", "done", "confirmed", "confirm booking",
        "please confirm", "confirm it",
    } or _msg_is_confirm and len(user_msg_lower.strip().split()) <= 2
    if not chat_session.missing_fields and _msg_is_confirm:
        if _llm_undecided or _is_pure_confirm:
            if not _is_edit_mixed:
                intent = "CONFIRM_BOOKING"
                chat_session.current_intent = intent
        elif _is_pure_confirm and intent != "CANCEL_BOOKING":
            # Even when LLM was "decisive" but message is unambiguous confirm,
            # honor it — decisive EDIT with edit words stays EDIT (handled above).
            if not _is_edit_mixed:
                intent = "CONFIRM_BOOKING"
                chat_session.current_intent = intent

    # ── Edit-details override (LLM-primary, keywords as fallback) ──
    # Bare "change"/"edit"/"modify" at confirmation must enter edit flow.
    # _is_edit_details_request only covered "change details" etc, so bare
    # "change" fell through to REQUEST_CONFIRMATION and looped forever.
    # Still fallback-only: a decisive LLM verdict (e.g. CONFIRM_BOOKING for
    # "yes, all good") is never flipped by an "edit" substring.
    _is_bare_edit = user_msg_lower.strip() in {"change", "edit", "modify", "update", "change details", "edit details"}
    if not chat_session.missing_fields and _llm_undecided and (_is_edit_details_request(user_msg_lower) or _is_bare_edit):
        # Bare "change" without a field: ask which field with a picker,
        # don't assume "name" and don't fall through to deterministic name ask.
        if _is_bare_edit and not _is_edit_details_request(user_msg_lower):
            msg = "Sure, which field would you like to change? You can say name, email, phone, date, or time."
            action = ActionPayload(
                message=msg,
                widget_directive="TEXT_INPUT",
                field_meta={"name": "edit_choice", "type": "text", "label": "Field to change"},
            )
            await _save_transcript(db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT", intent="EDIT_DATA")
            return action
        intent = "EDIT_DATA"
        chat_session.current_intent = intent

    # ── Resend OTP override (channel-aware, LLM-primary) ──────
    # Fires only when an OTP is genuinely pending AND the LLM did not
    # decisively classify the turn as something else (confirm/cancel/edit/
    # lookup) — so "yes, send it" on the confirm screen confirms instead of
    # resending, while a bare "resend" (LLM: PROVIDE_DATA) still resends.
    _phone_verified = getattr(chat_session, "phone_otp_verified", False)
    _email_needs_resend = require_otp and not chat_session.otp_verified
    _phone_needs_resend = require_phone_otp and not _phone_verified
    _llm_claimed_turn = intent not in _LLM_INDECISIVE_INTENTS and not llm_temporary_fallback
    _has_resend_kw = any(kw in user_msg_lower for kw in RESEND_KEYWORDS)
    if not _has_resend_kw and ("otp" in user_msg_lower or "code" in user_msg_lower):
        _neg = (
            "didn't" in user_msg_lower
            or "didnt" in user_msg_lower
            or "did not" in user_msg_lower
            or "not received" in user_msg_lower
            or "not got" in user_msg_lower
            or "never" in user_msg_lower
            or "no otp" in user_msg_lower
            or "no code" in user_msg_lower
            or "haven't" in user_msg_lower
            or "hasn't" in user_msg_lower
            or "waiting" in user_msg_lower
            or "where" in user_msg_lower
        )
        _has_resend_kw = bool(_neg)
    if (_email_needs_resend or _phone_needs_resend) and not _llm_claimed_turn and _has_resend_kw:
        # Determine which channel to resend: email first if pending, else phone
        _resend_channel = "email" if _email_needs_resend else "phone"
        # If message explicitly mentions phone/sms, override to phone
        if "phone" in user_msg_lower or "sms" in user_msg_lower or "mobile" in user_msg_lower:
            _resend_channel = "phone" if _phone_needs_resend else "email"
        return await _handle_otp_resend(
            db, org, chat_session, message_text, background_tasks,
            channel=_resend_channel, transcript_user_text=_transcript_user_text,
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
            db, chat_session.session_id, _transcript_user_text, msg, "REQUIRE_DATE_PICKER"
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
            db, chat_session.session_id, _transcript_user_text, msg, changed_directive
        )
        return action

    # ── Ask-AI mode (legacy path) ──
    # Ask-AI sessions are Q&A-only: no field-collection UI, no OTP
    # modals, no booking execution. Mirrors the LangGraph runtime's
    # downgrade (see langgraph_orchestrator._BOOKING_DIRECTIVES).
    _legacy_ask_mode = False
    try:
        _cd_legacy = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
        _legacy_ask_mode = str(_cd_legacy.get("_mode") or "").strip().lower() == "ask"
    except Exception:
        _legacy_ask_mode = False

    # ── OTP Verification (channel-aware, email then phone) ─
    _phone_verified = getattr(chat_session, "phone_otp_verified", False)
    _needs_email_otp = require_otp and not chat_session.otp_verified
    _needs_phone_otp = require_phone_otp and not _phone_verified
    _needs_any_otp = _needs_email_otp or _needs_phone_otp
    match = None
    if (
        _needs_any_otp
        and chat_session.session_status == "ACTIVE"
        and (not chat_session.missing_fields or len(chat_session.missing_fields) == 0)
        and not _legacy_ask_mode
    ):
        import re

        otp_pattern = re.compile(r"\b(\d{6})\b")
        match = otp_pattern.search(message_text)

        if match:
            otp_code = match.group(1)
            from app.crud.otp import verify_otp as crud_verify_otp

            # Determine expected channel: email first if pending, else phone
            _expected_channel = "email" if _needs_email_otp else "phone"
            # Try expected channel first, then fallback to other channel if both pending
            is_valid = False
            _verified_channel = None
            for _ch in ([_expected_channel] + (["phone"] if _expected_channel == "email" and _needs_phone_otp else ["email"] if _expected_channel == "phone" and _needs_email_otp else [])):
                try:
                    if await crud_verify_otp(db, chat_session.session_id, otp_code, channel=_ch):
                        is_valid = True
                        _verified_channel = _ch
                        break
                except ValueError as ve:
                    # Too many attempts — propagate as 429 later
                    raise ve
                except Exception:
                    continue
            if is_valid:
                # Reload the session row FIRST: crud_verify_otp() records the
                # verified-context key straight into the DB row, and this
                # turn's in-memory snapshot predates it. Without the reload,
                # _handle_booking_flow's context check sees a verified flag
                # with a missing key, revokes the verification, sends a new
                # code — and the user loops on OTP forever.
                try:
                    _fresh_session = await crud_chat.get_chat_session(db, chat_session.session_id)
                    if _fresh_session is not None:
                        chat_session = _fresh_session
                except Exception:
                    pass
                if _verified_channel == "phone":
                    chat_session.phone_otp_verified = True
                    logger.info(f"Phone OTP verified for session {chat_session.session_id}")
                    # If email also required and not yet verified, keep intent pending
                    if require_otp and not chat_session.otp_verified:
                        # Need email OTP still — trigger email OTP
                        from app.crud.otp import get_otp_policy_metadata as _get_meta
                        _meta = await _get_meta(db, chat_session.session_id, channel="email")
                        action = ActionPayload(
                            message="Phone verified! Now please verify your email. I've sent a code to your email.",
                            widget_directive="REQUIRE_OTP_MODAL",
                            field_meta={**_meta, "channel": "email"},
                        )
                        await _save_transcript(db, chat_session.session_id, _transcript_user_text, action.message, "REQUIRE_OTP_MODAL")
                        return action
                else:
                    chat_session.otp_verified = True
                    logger.info(f"Email OTP verified for session {chat_session.session_id}")
                    # If phone OTP also required, don't mark CONFIRM_BOOKING yet — trigger phone OTP
                    if require_phone_otp and not _phone_verified:
                        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
                        msg, directive, booking_field_meta = await _handle_booking_flow(
                            db, org, org_config, chat_session, "CONFIRM_BOOKING", require_otp, fd_map, background_tasks, require_phone_otp=require_phone_otp
                        )
                        # _handle_booking_flow will emit phone OTP modal
                        action = ActionPayload(message=msg, widget_directive=directive, field_meta=booking_field_meta)
                        await _save_transcript(db, chat_session.session_id, _transcript_user_text, msg, directive, intent="CONFIRM_BOOKING")
                        return action
                # If all required verifications done
                _still_needs_email = require_otp and not chat_session.otp_verified
                _still_needs_phone = require_phone_otp and not getattr(chat_session, "phone_otp_verified", False)
                if not _still_needs_email and not _still_needs_phone:
                    chat_session.current_intent = "CONFIRM_BOOKING"
                    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
                    logger.info(f"All OTP verified for session {chat_session.session_id}")

                    msg, directive, booking_field_meta = await _handle_booking_flow(
                        db, org, org_config, chat_session, "CONFIRM_BOOKING", require_otp, fd_map, background_tasks, require_phone_otp=require_phone_otp
                    )
                    action = ActionPayload(message=msg, widget_directive=directive, field_meta=booking_field_meta)
                    await _save_transcript(db, chat_session.session_id, _transcript_user_text, msg, directive, intent="CONFIRM_BOOKING")
                    return action
                # Fallthrough if still pending other channel (already handled above)
            else:
                from app.crud.otp import get_otp_policy_metadata

                # Determine which channel's policy to show
                _fail_channel = _expected_channel
                _meta = await get_otp_policy_metadata(db, chat_session.session_id, channel=_fail_channel)
                action = ActionPayload(
                    message="Invalid or expired OTP code. Please try again or say 'resend' to get a new code.",
                    widget_directive="REQUIRE_OTP_MODAL",
                    field_meta={**_meta, "channel": _fail_channel},
                )
                await _save_transcript(db, chat_session.session_id, _transcript_user_text, action.message, "REQUIRE_OTP_MODAL")
                return action

    # ── Data Validation ───────────────────────────────────────
    # Filter out LLM hallucinations: only keep entities for the primary
    # missing field (or fields the user could reasonably have answered).
    # This prevents the LLM from filling all 9 missing fields with "hiii".
    primary_missing = (
        chat_session.missing_fields[0] if chat_session.missing_fields else None
    )
    # Greeting guard (F2): a greeting-only message ("Hii..", "hello!") carries
    # no identity — drop any LLM-extracted `name` so greetings can never
    # poison the name slot and confuse later turns. Same for reserved words:
    # the model sometimes echoes the topic word itself ("Email ka code..."
    # -> name="Email"), which is never a person's name.
    if isinstance(raw_entities, dict) and "name" in raw_entities and (
        _is_greeting_only(message_text)
        or _is_reserved_name_value(raw_entities.get("name"))
    ):
        logger.info("greeting guard: dropping bad LLM-extracted name value")
        raw_entities = {k: v for k, v in raw_entities.items() if k != "name"}
    if raw_entities and primary_missing:
        filtered_entities = {}
        _missing_set = set(chat_session.missing_fields or [])
        for field, value in raw_entities.items():
            if field in _missing_set:
                # Any still-missing field can be auto-picked in one turn
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
        # Same admin same-day policy as the graph path (default deny,
        # matching _slot_policy_kwargs).
        allow_same_day_booking=bool(
            getattr(org_config, "allow_same_day_booking", False)
        ),
    )
    logger.info(f"Valid fields: {list(valid_data.keys())}")
    if errors:
        logger.warning(f"Validation Errors: {list(errors.keys())}")

    # A typed time must be one we actually offered. Format validation alone
    # accepts any well-formed clock time, so "11:11 PM" passed as a valid time
    # and the customer reached the summary with a slot that does not exist —
    # only to fail at the booking step with no explanation.
    #
    # Reject it here, where the time is stored, and re-offer the real options.
    _time_rejection = ""
    if valid_data.get("time") and not errors.get("time"):
        from app.engine.langgraph_orchestrator import _unmatched_time_choice as _utc

        _offered = _offered_slot_labels(chat_session, field_meta)
        if _offered:
            _rejected = _utc(message_text, _offered)
            if _rejected:
                _time_rejection = _rejected
                valid_data.pop("time", None)
                valid_data.pop("selected_slot_id", None)
                errors["time"] = "time_not_offered"
        else:
            # Nothing was offered on this turn (for example the customer typed
            # a time before any list existed). Fall back to the last list we
            # rendered for this session so a stale choice is still caught.
            _offered = _offered_slot_labels(chat_session, None)
            if _offered:
                _rejected = _utc(message_text, _offered)
                if _rejected:
                    _time_rejection = _rejected
                    valid_data.pop("time", None)
                    valid_data.pop("selected_slot_id", None)
                    errors["time"] = "time_not_offered"
                else:
                    _time_rejection = ""

    # Update session state with validated data
    chat_session.collected_data.update(valid_data)

    # Recalculate missing fields
    tenant_required_fields = get_required_field_names(field_definitions)
    chat_session.missing_fields = StateEvaluator.calculate_missing_fields(
        tenant_required_fields, chat_session.collected_data
    )

    if _time_rejection:
        # Ask for the time again and put the real options back in front of the
        # customer, instead of silently re-listing them as if nothing happened.
        chat_session.missing_fields = ["time"] + [
            f for f in (chat_session.missing_fields or []) if f != "time"
        ]
        chat_session.current_intent = "START_BOOKING"
        _offered_now = _offered_slot_labels(chat_session, field_meta)
        if _offered_now:
            chat_session.collected_data["offered_time_slots"] = _offered_now
            _reject_msg = (
                f"{_time_rejection} is not a valid time.\n\n"
                f"Please choose from the available slots:\n"
                + "\n".join(f"• {label}" for label in _offered_now)
                + f"\n\nReply with one of these (e.g., {_offered_now[0]})"
            )
            _reject_directive = "REQUIRE_TIME_SLOT_GRID"
        else:
            _reject_msg = (
                f"{_time_rejection} is not a valid time. Please share one of the "
                f"available slots."
            )
            _reject_directive = "REQUIRE_TIME_SLOT_GRID"
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        _reject_meta = {
            "name": "time",
            "type": "time",
            "label": "Preferred Time",
            "invalid_choice": _time_rejection,
        }
        if _offered_now:
            _reject_meta["available_slots"] = _offered_now
        return ActionPayload(
            message=_reject_msg,
            widget_directive=_reject_directive,
            field_meta=_reject_meta,
        )

    # Persist
    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    logger.info("--- SESSION STATE UPDATED ---")
    logger.info(f"Intent: {chat_session.current_intent}")
    logger.info(f"Collected fields: {list(chat_session.collected_data.keys())}")
    logger.info(f"Missing: {chat_session.missing_fields}")
    logger.info("-----------------------------")

    # ── Hybrid meeting type selection (before booking) ──
    # If all required fields are collected but meeting_type is still missing for HYBRID with choice allowed, ask for it
    _need_meeting_type = False
    _meeting_mode_check = _normalize_meeting_mode(getattr(org_config, "meeting_mode", None))
    _allow_hybrid_check = bool(getattr(org_config, "allow_hybrid_selection", False))
    if not chat_session.missing_fields and not chat_session.collected_data.get("meeting_type"):
        if _meeting_mode_check == MeetingMode.HYBRID and _allow_hybrid_check:
            _need_meeting_type = True
            # Check if user just provided meeting_type directly (Online/Offline) via select
            _msg_lower = message_text.strip().lower()
            if _msg_lower in ("online", "offline"):
                _norm_type = "ONLINE" if _msg_lower == "online" else "OFFLINE"
                chat_session.collected_data["meeting_type"] = _norm_type
                await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
                _need_meeting_type = False
            else:
                # Also check LLM extracted entities for meeting_type
                _maybe_type = None
                if isinstance(raw_entities, dict):
                    _maybe_type = raw_entities.get("meeting_type")
                if isinstance(_maybe_type, str) and _maybe_type.strip().lower() in ("online", "offline"):
                    _norm_type = "ONLINE" if _maybe_type.strip().lower() == "online" else "OFFLINE"
                    chat_session.collected_data["meeting_type"] = _norm_type
                    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
                    _need_meeting_type = False
                    # Remove from raw_entities so it doesn't interfere with other validation
                    raw_entities.pop("meeting_type", None)

    # ── Determine next action ─────────────────────────────────
    if _need_meeting_type:
        next_step = {
            "directive": "REQUIRE_SELECT",
            "field_meta": {
                "name": "meeting_type",
                "type": "select",
                "label": "Meeting Type",
                "placeholder": "Choose meeting type",
                "description": "How would you like to meet?",
                "icon": "💻",
                "options": ["Online", "Offline"],
                "required": True,
            },
        }
    else:
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

    # ── Ghost suppression during OTP pending ────────────────
    # A content-free "I have no information" reply is never the right answer
    # when the customer is one code away from booking. The canned apology is no
    # longer produced anywhere in the product; this only catches a model
    # reproducing the phrasing on its own.
    _needs_otp_now = (require_otp and not chat_session.otp_verified) or (require_phone_otp and not getattr(chat_session, "phone_otp_verified", False))
    if _needs_otp_now and isinstance(llm_generated_msg, str):
        from app.engine.knowledge_guards import is_knowledge_fallback_text as _is_kb_fallback

        if _is_kb_fallback(llm_generated_msg):
            _channel = "phone" if (require_phone_otp and not getattr(chat_session, "phone_otp_verified", False)) else "email"
            _dest = str(chat_session.collected_data.get("phone") or _resolve_session_email(chat_session, fd_map) or "your contact").strip()
            llm_generated_msg = f"Please enter the verification code sent to {_dest}."
            if next_step.get("directive") == "TEXT_INPUT":
                next_step = {"directive": "REQUIRE_OTP_MODAL", "field_meta": {"channel": _channel}}

    # ── Hallucinated slot timings guard ───────────────
    # LLM sometimes invents times like "9am, 10am" not in the real inventory
    # (end-of-conversation hallucination). If directive is slot-related and
    # field_meta has deterministic options, ensure msg doesn't list invented times.
    _directive_now = str(next_step.get("directive") or "")
    if _directive_now in ("REQUIRE_TIME_SLOT_GRID", "REQUIRE_DATE_PICKER"):
        _opts = (next_step.get("field_meta") or {}).get("options") or []
        if _opts and isinstance(llm_generated_msg, str):
            import re as _re_slot
            _msg_times = _re_slot.findall(r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\b|\b\d{1,2}\s*(?:AM|PM|am|pm)\b", llm_generated_msg)
            if _msg_times:
                _opt_set = {str(o).lower().strip() for o in _opts}
                # Normalize msg times for comparison (e.g. "9am" → "9:00 AM" not exact, so use substring check)
                _hallucinated = False
                for _t in _msg_times:
                    _tl = _t.lower().strip()
                    if not any(_tl in _o or _o in _tl for _o in _opt_set):
                        _hallucinated = True
                        break
                if _hallucinated:
                    _slot_list = ", ".join(str(o) for o in _opts[:6])
                    llm_generated_msg = f"Here are the available slots: {_slot_list}. Please choose one."
                    logger.warning(f"slot hallucination sanitized: invented times stripped, replaced with real slots")

    if errors:
        # Only show validation errors when the user actually tried to provide
        # data for a field but it was invalid (bad format, out of range, etc.).
        # If the user didn't provide any value at all (just asked a question,
        # complained, etc.), let the LLM's conversational response through.
        # Check ALL fields the user tried to provide, not just the primary
        # missing field — this handles cases where a previously-valid field
        # (e.g. email) is re-entered incorrectly.
        user_tried_fields = set(raw_entities.keys()) if isinstance(raw_entities, dict) else set()
        error_fields = set(errors.keys())
        invalid_user_fields = user_tried_fields & error_fields

        if invalid_user_fields:
            # Show the first invalid field's error (deterministic order)
            for field in raw_entities:
                if field in errors:
                    error_msg = errors[field]
                    msg = f"I couldn't quite understand that:\n{error_msg}\nPlease try again."
                    directive = next_step.get("directive", "TEXT_INPUT")
                    break
        else:
            # User didn't try to provide data — conversational turn.
            # Deterministic next-field guard (F1): in collection intents the
            # reply must ask for the true next missing field. Free-form prose
            # that re-asks an already-collected field (the "stuck on email"
            # loop) is replaced with the deterministic single-field ask.
            # A declined session gets answers only — never interrogation.
            # Otherwise the deterministic ask is APPENDED (never replacing),
            # so answers and acknowledgments are never squashed by the flow.
            _need_field = (chat_session.missing_fields or [None])[0]
            _fm = next_step.get("field_meta") if isinstance(next_step, dict) else None
            from app.engine import decline_flow as _decline_flow
            from app.engine.user_visible_text import looks_like_json_debris
            from app.engine.user_visible_text import (
                looks_like_field_waiver as _waiver_legacy,
            )

            # Required-field waiver seatbelt (legacy path): the model agreed
            # to drop a still-missing REQUIRED field ("I can skip the phone
            # number too"). Same rule as the graph-path guard — replace with
            # a firm re-ask instead of letting the waiver stand. Optional
            # waivers pass through untouched.
            _required_legacy: set = set()
            try:
                _required_legacy = {
                    str(_fd.name)
                    for _fd in (field_definitions or [])
                    if bool(getattr(_fd, "required", True))
                }
            except Exception:
                _required_legacy = set()
            _waived_legacy = bool(
                _need_field
                and _waiver_legacy(
                    llm_generated_msg,
                    missing_fields=list(chat_session.missing_fields or []),
                    required_names=_required_legacy,
                )
            )

            # Debris check: raw JSON containing the field name would fool the
            # cue test into thinking the reply asks for the field.
            _visibly_asks_legacy = _response_asks_for_field(
                llm_generated_msg, _need_field, (_fm or {}).get("label")
            ) and not looks_like_json_debris(llm_generated_msg)
            # Ask-AI mode: the reply is the product — never patch it with
            # deterministic collection copy.
            _legacy_ask_mode = False
            try:
                _cd_lam = getattr(chat_session, "collected_data", None) or {}
                _legacy_ask_mode = (
                    isinstance(_cd_lam, dict)
                    and str(_cd_lam.get("_mode") or "").strip().lower() == "ask"
                )
            except Exception:
                _legacy_ask_mode = False
            if _waived_legacy:
                _need_label_legacy = str(
                    ((_fm or {}).get("label") or _need_field or "detail")
                ).strip().lower()
                msg = (
                    f"I'll still need your {_need_label_legacy} — I can't "
                    f"confirm the booking without it. Could you please share it?"
                )
                directive = (
                    next_step.get("directive", "TEXT_INPUT")
                    if isinstance(next_step, dict)
                    else "TEXT_INPUT"
                )
            elif (
                intent in _COLLECTION_INTENTS
                and _need_field
                and isinstance(next_step, dict)
                and not _decline_flow.is_declined(chat_session)
                and not _visibly_asks_legacy
                and not _legacy_ask_mode
            ):
                _det_ask = _build_context_aware_response(
                    directive=next_step.get("directive", "TEXT_INPUT"),
                    field_meta=_fm,
                    collected_data=chat_session.collected_data,
                    valid_data=valid_data,
                    errors=errors,
                    llm_generated_msg=llm_generated_msg,
                    agent_name=persona.get("agent_name", "Booking Assistant"),
                )
                # Machine debris is never appended — replace unconditionally.
                if not _det_ask.strip():
                    msg = llm_generated_msg
                elif (isinstance(errors, dict) and errors.get(_need_field)) or looks_like_json_debris(
                    llm_generated_msg
                ):
                    logger.info(
                        "next_field_guard: free-form reply missed '%s'; using deterministic ask",
                        _need_field,
                    )
                    msg = _det_ask
                else:
                    msg = f"{llm_generated_msg}\n\n{_det_ask}"
            else:
                # Let the LLM handle it naturally (answer questions, explain why
                # fields are needed, handle complaints).
                directive = next_step.get("directive", "TEXT_INPUT")
                msg = llm_generated_msg
    elif next_step["directive"] == "PROCESS_BOOKING" and not _legacy_ask_mode:
        # A field the customer explicitly asked to change was just updated.
        # The LLM quite reasonably reads a complete, edited booking as
        # "PROCESS_BOOKING" — but that must NOT book it. The customer has not
        # seen the change yet and has not confirmed it, so the revised summary
        # is shown first and only a later confirmation may authorise a booking.
        #
        # Previously the EDIT_DATA re-summary lived only in the `else` branch
        # below and was skipped here, so the customer changed the time and got
        # "Your appointment time is now updated... anything else?" with no
        # summary and no way to confirm. The booking then only completed by
        # luck on a later turn.
        if (
            intent == "EDIT_DATA"
            and valid_data
            and not chat_session.missing_fields
            and not _is_unverified_booking_claim(chat_session)
        ):
            msg, directive = _build_edit_summary(chat_session, valid_data, fd_map)
        else:
            msg, directive, booking_field_meta = await _handle_booking_flow(
                db,
                org,
                org_config,
                chat_session,
                intent,
                require_otp,
                fd_map,
                background_tasks,
                require_phone_otp=require_phone_otp,
            )
    else:
        directive = next_step["directive"]

        if manual_time_requires_slot_selection:
            directive = "REQUIRE_TIME_SLOT_GRID"
            msg = "Please select your preferred time from the available slot below."
        elif llm_temporary_fallback:
            if str(getattr(chat_session, "session_status", "") or "").upper() in (
                "CONFIRMED", "CANCELLED", "EXPIRED",
            ):
                msg = build_finished_session_reply(chat_session, chat_session.session_status)
            else:
                msg = _build_llm_recovery_message(directive, next_step.get("field_meta"))
        elif valid_data:
            # User provided valid data — use deterministic response to ask
            # for the next field in order. This keeps the flow stable.
            _valid_ask = _build_context_aware_response(
                directive=directive,
                field_meta=next_step.get("field_meta"),
                collected_data=chat_session.collected_data,
                valid_data=valid_data,
                errors=errors,
                llm_generated_msg=llm_generated_msg,
                agent_name=persona.get("agent_name", "Booking Assistant"),
            )
            msg = _valid_ask if _valid_ask.strip() else llm_generated_msg
        else:
            # User didn't provide data (greeting, etc.) -> still enforce one-by-one deterministic ask
            # to prevent LLM from dumping "provide name, email, phone..." at once.
            single_field_directives = {
                "TEXT_INPUT",
                "REQUIRE_EMAIL_INPUT",
                "REQUIRE_PHONE_INPUT",
                "REQUIRE_DATE_PICKER",
                "REQUIRE_TIME_SLOT_GRID",
                "REQUIRE_SELECT",
                "REQUIRE_TEXTAREA",
                "REQUIRE_NUMBER_INPUT",
            }
            if directive in single_field_directives:
                _single_ask = _build_context_aware_response(
                    directive=directive,
                    field_meta=next_step.get("field_meta"),
                    collected_data=chat_session.collected_data,
                    valid_data=valid_data,
                    errors=errors,
                    llm_generated_msg=llm_generated_msg,
                    agent_name=persona.get("agent_name", "Booking Assistant"),
                )
                msg = _single_ask if _single_ask.strip() else llm_generated_msg
            else:
                # For ASK_QUESTION / TEXT_INPUT free chat, let LLM handle naturally
                msg = llm_generated_msg

        # Re-show summary after EDIT_DATA only after an actual field change.
        if intent == "EDIT_DATA" and valid_data and not chat_session.missing_fields:
            msg, directive = _build_edit_summary(chat_session, valid_data, fd_map)
        elif intent == "EDIT_DATA" and not valid_data:
            directive = "TEXT_INPUT"
            msg = "Sure - tell me what you'd like to change (name, email, phone, date, or time)."
        elif directive == "REQUEST_CONFIRMATION":
            msg, directive = _build_confirmation_summary(chat_session, fd_map)

    # If payment required and not yet paid, override directive to REQUIRE_PAYMENT even when next_step is PROCESS_BOOKING
    # This handles the case where LLM thinks booking is ready but payment not verified
    try:
        _price_enabled2 = bool(getattr(org_config, "appointment_price_enabled", False))
        _price_paise2 = int(getattr(org_config, "appointment_price_paise", 0) or 0)
        _currency2 = (getattr(org_config, "appointment_currency", "INR") or "INR").upper()
        # Provider-aware readiness + catalog charge (same as booking gate).
        _ready2, _provider2 = await payment_service.is_usable(db, org.id, org_config)
        if _price_enabled2:
            try:
                _cd2 = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
                _guid2 = getattr(chat_session, "unit_id", None) or _cd2.get("unit_id")
                _gpid2 = getattr(chat_session, "provider_id", None) or _cd2.get("provider_id")
                _price_paise2, _currency2 = await payment_service.resolve_charge(
                    db, org.id, org_config, _guid2, _gpid2
                )
            except Exception:
                pass
        if _price_enabled2 and _price_paise2 > 0 and _ready2:
            from app.models.payment import PaymentAttempt as _PA2
            from sqlalchemy.future import select as _sel2
            _paid2 = (await db.execute(_sel2(_PA2).where(_PA2.session_id == chat_session.session_id, _PA2.org_id == org.id, _PA2.status == "paid", _PA2.amount_paise == _price_paise2))).scalars().first()
            if not _paid2 and directive == "REQUEST_CONFIRMATION":
                directive = "REQUIRE_PAYMENT"
                msg = f"Your appointment total is ₹{_price_paise2/100:.2f}. Please pay to confirm your booking."
                field_meta = {"amount_paise": _price_paise2, "amount_display": f"₹{_price_paise2/100:.2f}", "currency": _currency2, "payment_provider": _provider2, "session_id": str(chat_session.session_id)}
    except Exception:
        pass

    field_meta = booking_field_meta if 'booking_field_meta' in locals() and booking_field_meta is not None else next_step.get("field_meta") if 'next_step' in locals() and isinstance(next_step, dict) else None
    has_collected_data = bool(chat_session.collected_data)
    collected_keys = (
        list(chat_session.collected_data.keys()) if chat_session.collected_data else []
    )
    logger.info(
        f"DEBUG: directive={directive}, field_meta has options: {field_meta.get('options') if field_meta else None}, has_collected_data={has_collected_data}, collected_keys={collected_keys}"
    )

    # WhatsApp parity: offer upcoming dates as tappable options. The widget
    # renders its own calendar and ignores `options` on date fields, so this
    # is a no-op everywhere except WhatsApp list rendering.
    if (
        directive == "REQUIRE_DATE_PICKER"
        and isinstance(field_meta, dict)
        and not field_meta.get("options")
    ):
        field_meta["options"] = _upcoming_date_options(
            org_timezone, max_booking_days
        )

    # For date/time asks on WhatsApp, include a concise hours line contextually
    # so the user sees hours with the picker, per message flow. No full paragraph
    # at start — each step shows only its own options.
    if directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"):
        try:
            # Use a short hours line, not the full verbose block
            _hours_short = "Hours: Mon-Fri 9:00 AM–5:00 PM, Sat 9:00 AM–4:00 PM, Sun Closed (Asia/Calcutta)"
            # Try to build a concise line from actual schedule if available
            try:
                _full_hours = await load_weekly_hours_block(db, str(org.id) if org else "")
                if _full_hours and "Monday" in _full_hours:
                    # Extract first line which has the summary, keep it short
                    _first_line = _full_hours.split("\n")[0].replace("## ", "").replace(" (authoritative — answer timing questions from this)", "")
                    if len(_first_line) < 120:
                        _hours_short = _first_line.strip()
            except Exception:
                pass
            if isinstance(msg, str) and "Hours:" not in msg and "business hours" not in msg.lower():
                # Prepend concise hours line to the ask
                msg = f"{_hours_short}\n{msg}"
        except Exception:
            pass

    # Org dept flow (legacy path mirror): team-member choice comes AFTER the
    # date. Swap the slot grid for a member picker when the session carries a
    # department but no member yet.
    if directive == "REQUIRE_TIME_SLOT_GRID":
        try:
            from app.services.catalog_service import providers_for_date as _pfd_legacy
            from dateutil.parser import parse as _parse_legacy

            _lu = getattr(chat_session, "unit_id", None) or (chat_session.collected_data or {}).get("unit_id")
            _lp = getattr(chat_session, "provider_id", None) or (chat_session.collected_data or {}).get("provider_id")
            _ld_raw = (chat_session.collected_data or {}).get("date")
            if _lu and not _lp and _ld_raw:
                _ld = _parse_legacy(str(_ld_raw)).date()
                _lprovs = await _pfd_legacy(db, org.id, _lu, _ld)
                if not _lprovs:
                    directive = "REQUIRE_DATE_PICKER"
                    try:
                        chat_session.collected_data.pop("date", None)
                        chat_session.collected_data.pop("time", None)
                        chat_session.collected_data.pop("selected_slot_id", None)
                        chat_session.missing_fields = ["date"] + [
                            f for f in (chat_session.missing_fields or []) if f != "date"
                        ]
                        await crud_chat.update_chat_session(
                            db, chat_session.session_id, chat_session
                        )
                    except Exception:
                        pass
                    if not field_meta:
                        field_meta = {"name": "date", "type": "date", "label": "Preferred Date"}
                    field_meta.update(
                        {
                            "name": "date",
                            "type": "date",
                            "label": "Preferred Date",
                            "validation_error": "No team members from this department work on that date. Please pick another date.",
                            "follow_up_fields": ["date"],
                        }
                    )
                    msg = (
                        "No team members from this department work on that date. "
                        "Please choose another date."
                    )
                elif _lprovs:
                    try:
                        _cd_l = dict(chat_session.collected_data or {})
                        _cd_l["_provider_options"] = [
                            {"id": str(p.get("id")), "full_name": p.get("full_name")}
                            for p in _lprovs
                        ]
                        _cd_l["_provider_unit"] = str(_lu)
                        chat_session.collected_data = _cd_l
                        await crud_chat.update_chat_session(
                            db, chat_session.session_id, chat_session
                        )
                    except Exception:
                        pass
                    directive = "REQUIRE_PROVIDER_PICKER"
                    _llines = []
                    for _i, _p in enumerate(_lprovs, start=1):
                        _ll = str(_p.get("full_name") or "").strip()
                        if _p.get("fee_display"):
                            _ll += f" · {_p['fee_display']}"
                        _llines.append(f"{_i}. {_ll}")
                    _d_nice = _ld.strftime("%A, %B %d")
                    msg = f"Who would you like to see on {_d_nice}?\n" + "\n".join(_llines)
                    if str(getattr(chat_session, "channel", "") or "").lower() not in ("whatsapp", "whatsapp_cloud"):
                        msg = f"Who would you like to see on {_d_nice}?"
                    if not field_meta:
                        field_meta = {}
                    field_meta.update(
                        {
                            "name": "provider",
                            "type": "provider",
                            "label": "Choose team member",
                            "unit_id": str(_lu),
                            "providers": _lprovs,
                        }
                    )
        except Exception as _legacy_swap_err:
            logger.warning(f"provider_swap_lookup_failed (legacy): {_legacy_swap_err}")

    # Fetch available time slots when directive is REQUIRE_TIME_SLOT_GRID
    # For WhatsApp, ensure we always have a list even if field_meta was empty (the widget's calendar ignores it, but WA needs it)
    if directive == "REQUIRE_TIME_SLOT_GRID":
        if not field_meta:
            field_meta = {"name": "time", "type": "time", "label": "Preferred Time"}
        target_date = chat_session.collected_data.get("date")
        logger.info(f"DEBUG: target_date={target_date}, org.id={org.id} directive={directive} has_options={bool(field_meta.get('options')) if isinstance(field_meta, dict) else False}")
        if target_date and (not field_meta.get("options") if isinstance(field_meta, dict) else True):
            from dateutil.parser import parse as date_parse
            from app.engine.slot_engine import (
                compute_slot_inventory,
                format_slot_time,
                apply_scheduling_policy_to_inventory,
            )

            try:
                parsed_date = date_parse(target_date).date()
                try:
                    _leg_prov = getattr(chat_session, "provider_id", None) or (chat_session.collected_data or {}).get("provider_id")
                except Exception:
                    _leg_prov = None
                inventory, availability_status = await compute_slot_inventory(
                    db, org.id, parsed_date, org_config=org_config,
                    provider_id=_leg_prov,
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
                # Convert UTC slot times to the effective (business/user) timezone
                # so WhatsApp shows local times, not UTC (e.g., 9:30 AM IST, not 3:30 AM UTC)
                try:
                    _tz_for_slots = ZoneInfo(str(timezone_resolution.get("effective_timezone") or org_timezone or "UTC"))
                except Exception:
                    _tz_for_slots = timezone.utc
                options = []
                for item in inventory:
                    if not item.get("available"):
                        continue
                    _start = item.get("start_utc")
                    if _start is None:
                        continue
                    try:
                        if getattr(_start, "tzinfo", None) is None:
                            _start = _start.replace(tzinfo=timezone.utc)
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
                    if availability_status == "closed_day":
                        msg = (
                            f"We are closed on {parsed_date.strftime('%A, %B %d')}. "
                            "Please choose another date."
                        )
                    elif availability_status == "exception_closed":
                        msg = (
                            f"We are closed on {parsed_date.strftime('%A, %B %d')}. "
                            "Please choose another date."
                        )
                    else:
                        msg = (
                            "There are no available times on that date. "
                            "Please choose another date."
                        )
                    field_meta["closed_reason"] = availability_status
                    chat_session.collected_data.pop("date", None)
                    chat_session.collected_data.pop("time", None)
                    chat_session.collected_data.pop("selected_slot_id", None)
                    if "date" not in chat_session.missing_fields:
                        chat_session.missing_fields = ["date"] + list(
                            chat_session.missing_fields or []
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

    # ── Ask-AI last-mile guard (legacy path) ──
    # No booking-family directive may leave this function in ask mode,
    # no matter which branch above produced it. Same rule as the LangGraph
    # runtime (single source of truth). Lazy import: langgraph_orchestrator
    # imports this module at top level, so a top-level import would cycle.
    # The reply text passes through untouched.
    if _legacy_ask_mode:
        try:
            from app.engine.langgraph_orchestrator import (
                downgrade_ask_directive as _downgrade_ask,
            )

            _new_directive = _downgrade_ask(mode="ask", directive=directive)
        except Exception:
            _new_directive = directive
        if _new_directive != directive:
            try:
                logger.info(
                    "ask_mode.legacy_downgrade_booking_directive",
                    extra={
                        "session_id": str(chat_session.session_id),
                        "directive": directive,
                    },
                )
            except Exception:
                pass
            directive = _new_directive

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
    channel: str = "email",
    transcript_user_text: Optional[str] = None,
) -> ActionPayload:
    _transcript_user_text = (
        transcript_user_text if transcript_user_text is not None else message_text
    )
    from app.crud.otp import generate_otp, get_otp_policy_metadata, invalidate_otp_delivery, OTP_RESEND_CAP
    from app.core.otp_delivery import delivery_error, delivery_succeeded
    from app.core.config import settings as _otp_settings

    channel = (channel or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"
    try:
        if channel == "phone":
            phone = str(chat_session.collected_data.get("phone") or "").strip()
            if not phone:
                action = ActionPayload(
                    message="Please share your phone number.",
                    widget_directive="REQUIRE_PHONE_INPUT",
                )
                await _save_transcript(db, chat_session.session_id, _transcript_user_text, action.message, action.widget_directive)
                return action
            code = await generate_otp(db, chat_session.session_id, channel="phone", resend=True)
            policy_meta = await get_otp_policy_metadata(db, chat_session.session_id, channel="phone")
            policy_meta["channel"] = "phone"
            _via_resend = "WhatsApp"
            resend_attempt = OTP_RESEND_CAP - int(policy_meta.get("resends_left", 0))
            otp_send_key = f"otp_phone:{chat_session.session_id}:resend:{resend_attempt}"
            # Phone OTP via WhatsApp is sent by the caller (_handle_booking_flow) or by whatsapp_provider;
            # for resend we still need to actually send the code (background_tasks path handles it for email, but phone needs explicit send here)
            try:
                from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider as _WPPResend
                _prov_resend = _WPPResend()
                _result = await _prov_resend.send_otp(phone, code, str(chat_session.session_id))
                if not delivery_succeeded(
                    _result,
                    allow_mock=bool(getattr(_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
                ):
                    await invalidate_otp_delivery(
                        db,
                        chat_session.session_id,
                        "phone",
                        context_key=otp_send_key,
                    )
                    raise RuntimeError(delivery_error(_result))
            except Exception as _send_resend_err:
                logger.warning(f"Phone OTP resend send failed ({_via_resend}): {_send_resend_err}")
                raise
            action = ActionPayload(
                message=f"I've sent a new verification code to {phone} via {_via_resend}. Please enter it here.",
                widget_directive="REQUIRE_OTP_MODAL",
                field_meta=policy_meta,
            )
        else:
            from app.core.mail import send_otp_email
            recipient_email = _resolve_session_email(chat_session)
            code = await generate_otp(db, chat_session.session_id, channel="email", resend=True)
            policy_meta = await get_otp_policy_metadata(db, chat_session.session_id, channel="email")
            policy_meta["channel"] = "email"
            resend_attempt = OTP_RESEND_CAP - int(policy_meta.get("resends_left", 0))
            otp_send_key = f"otp:{chat_session.session_id}:resend:{resend_attempt}"
            if not recipient_email:
                await invalidate_otp_delivery(
                    db,
                    chat_session.session_id,
                    "email",
                    context_key=otp_send_key,
                )
                raise ValueError("Email address is required")
            _result = await send_otp_email(
                recipient_email,
                code,
                org_id=org.id,
                idempotency_key=otp_send_key,
            )
            if not delivery_succeeded(
                _result,
                allow_mock=bool(getattr(_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
            ):
                await invalidate_otp_delivery(
                    db,
                    chat_session.session_id,
                    "email",
                    context_key=otp_send_key,
                )
                raise RuntimeError(delivery_error(_result))
            action = ActionPayload(
                message=f"I've sent a new verification code to {recipient_email}. Please check your inbox.",
                widget_directive="REQUIRE_OTP_MODAL",
                field_meta=policy_meta,
            )
    except ValueError as otp_err:
        from app.crud.otp import get_otp_policy_metadata as _gm, OTP_COOLDOWN_SECONDS
        try:
            _meta = await _gm(db, chat_session.session_id, channel=channel)
            _meta["channel"] = channel
        except Exception:
            _meta = {"channel": channel, "cooldown_remaining_seconds": OTP_COOLDOWN_SECONDS}
        # Preserve specific cooldown text + surface remaining wait for UX
        raw = str(otp_err)
        if "cooldown" in raw.lower() and "wait" in raw.lower():
            # keep the remaining-seconds wording from crud
            pass
        else:
            # normalize generic cap message
            raw = raw.strip() or "Please wait before retrying."
        action = ActionPayload(
            message=raw,
            widget_directive="REQUIRE_OTP_MODAL",
            field_meta=_meta,
        )
    except Exception as otp_err:
        logger.error(f"OTP resend failed ({channel}): {otp_err}")
        action = ActionPayload(
            message="I'm still having trouble sending the code. Please verify your contact details are correct.",
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


def should_create_booking(
    *,
    intent: str,
    user_confirmed: Optional[bool],
    session_status: str,
    missing_fields,
    require_otp: bool,
    require_phone_otp: bool,
    otp_verified: bool,
    phone_otp_verified: bool,
) -> bool:
    """Decide whether this turn may create the booking.

    Creating a booking is the single most consequential action in the
    product, so the decision is driven by verified state rather than by the
    intent label the language model happened to emit. Keying it off
    ``intent == "CONFIRM_BOOKING"`` meant a model that returned anything else
    on the confirmation turn silently fell through to a summary card: no
    booking row, no calendar entry, no notification, no email, and no error
    anywhere.

    ``user_confirmed`` is supplied by the caller from the actual inbound text.
    When it is None the legacy intent check is used, so callers that predate
    this change behave exactly as before.

    Fails closed: every obligation (fields collected, OTP satisfied where
    required, session still active, user actually confirmed) must hold.
    """
    if str(session_status or "").upper() != "ACTIVE":
        return False

    if user_confirmed is None:
        # No confirmation signal available: preserve the previous behaviour.
        return intent == "CONFIRM_BOOKING"

    if not user_confirmed:
        # A bare 6-digit code is not a confirmation word, but accepting a code
        # IS the confirmation: the code is only ever sent after the customer
        # explicitly confirmed the summary. Blocking here on the code turn
        # re-showed the summary forever and no booking was ever created.
        if require_phone_otp and phone_otp_verified:
            pass
        elif require_otp and otp_verified:
            pass
        else:
            return False
    if missing_fields:
        return False
    if require_otp and not otp_verified:
        return False
    if require_phone_otp and not phone_otp_verified:
        return False
    return True


def _verified_key_matches_context(stored_key: str, context_key: str) -> bool:
    """Does a stored OTP-verified key belong to this customer in this session?

    A stored key has been written in two shapes:
      * the full context key  ``otp_phone:<session>:<phone>:<date>:<time>``
      * just the phone number  ``+916350063338``

    Comparing the two shapes directly meant a correctly verified customer was
    revoked on practically every booking, because the full key embeds the
    date and time: any difference in how the slot was stored between the
    verification turn and the booking turn (a date the customer changed, a time
    normalised from "10:30 AM" to "10:30") produced a mismatch, the
    verification was thrown away, a fresh code was sent, and the booking was
    never created.

    The check exists to stop verification being carried over from a DIFFERENT
    customer or conversation, so that is what it compares: the session and the
    phone number. Slot details are deliberately excluded, so editing the
    appointment after verifying does not silently discard a real verification.
    """
    stored = str(stored_key or "").strip()
    context = str(context_key or "").strip()
    if not stored or not context:
        return False

    def _parts(key: str) -> list:
        return [p for p in key.split(":")]

    stored_parts = _parts(stored)
    context_parts = _parts(context)

    # Session must match (or the stored value is a bare phone, which is
    # session-scoped by where it is stored).
    if len(context_parts) >= 2 and stored_parts and stored_parts[0].startswith("otp_"):
        if stored_parts[1].strip() != context_parts[1].strip():
            return False

    # Phone must match. Compare on digits so formatting is irrelevant.
    def _phone_of(parts: list) -> str:
        if len(parts) >= 3 and parts[0].startswith("otp_"):
            return re.sub(r"\D", "", parts[2])
        return re.sub(r"\D", "", parts[0])

    stored_phone = _phone_of(stored_parts)
    context_phone = _phone_of(context_parts)
    if not stored_phone or not context_phone:
        return False
    return stored_phone == context_phone


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
    require_phone_otp: bool = False,
    user_confirmed: Optional[bool] = None,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Handle the PROCESS_BOOKING directive — OTP (email/phone), confirmation, slot booking.

    user_confirmed: whether THIS turn's inbound text was a confirmation. When
    provided it drives the booking decision (see should_create_booking) instead
    of the model-supplied intent label. None keeps the legacy behaviour.
    """
    # Support caller not passing phone flag explicitly — read from config as fallback
    if not require_phone_otp:
        require_phone_otp = bool(getattr(org_config, "require_phone_otp", False))

    # Review gate FIRST (before slot lock): no DB touched on the reroute
    # path, so tests and lock-free callers stay safe, and no slot is held
    # while the customer is still only previewing.
    _review_seen_hbf = False
    try:
        _review_seen_hbf = bool(
            (chat_session.collected_data or {}).get("_review_presented")
        )
    except Exception:
        _review_seen_hbf = False
    if not (chat_session.missing_fields or []) and not _review_seen_hbf:
        _already_verified_hbf = bool(
            (require_otp and getattr(chat_session, "otp_verified", False))
            or (
                require_phone_otp
                and getattr(chat_session, "phone_otp_verified", False)
            )
        )
        if not _already_verified_hbf:
            _summary_msg_hbf, _summary_directive_hbf = _build_confirmation_summary(
                chat_session, fd_map
            )
            return _summary_msg_hbf, _summary_directive_hbf, None

    # Lock the slot BEFORE showing OTP modal or confirmation request
    # This prevents ghost slots during the waiting period
    await _lock_slot_for_booking(db, org, chat_session)

    # ── Review before verification ──────────────────────────────
    # Required order: collect details -> show the summary -> customer
    # confirms -> only THEN send the OTP -> customer enters it -> booking is
    # created.
    #
    # Previously the OTP was sent as soon as the fields were complete, so the
    # customer got a code first and the summary afterwards; confirming then
    # re-displayed the summary instead of moving on to verification.
    #
    # The mandatory preview now lives at the top of this function (before
    # slot lock): no prior card means reroute to the card first, even on an
    # explicit "Yes" (production: time saved → model said "I'll confirm" →
    # "Yes" → OTP, customer never saw the details). user_confirmed is
    # intentionally NOT consulted there; a yes-without-preview is the hole.
    _already_verified = bool(
        (require_otp and getattr(chat_session, "otp_verified", False))
        or (
            require_phone_otp
            and getattr(chat_session, "phone_otp_verified", False)
        )
    )

    # ── Email OTP gate ──
    recipient_email = _resolve_session_email(chat_session, fd_map)

    # ── Single active booking per customer ──
    # One user (same email OR phone) may hold only one upcoming appointment
    # at a time. Cancel deletes the row and reschedule deletes before
    # reopening, so any upcoming row found here belongs to an earlier
    # booking that must be cancelled first. Checked BEFORE any OTP is sent
    # so we never issue codes for a booking that cannot proceed.
    # Deterministic (DB truth) — the LLM must never freelance this reply.
    try:
        from app.engine.cancel_flow import _find_upcoming_booking as _find_dup_booking
        from app.engine.cancel_flow import _format_slot_label as _dup_slot_label

        _dup_booking, _dup_start = None, None
        _dup_phone = str((chat_session.collected_data or {}).get("phone") or "").strip()
        if recipient_email:
            _dup_booking, _dup_start = await _find_dup_booking(db, org, "email", recipient_email)
        if _dup_booking is None and _dup_phone:
            _dup_booking, _dup_start = await _find_dup_booking(db, org, "phone", _dup_phone)
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
                f"Choose what to do below."
            )
            # Pin the existing booking id so the reschedule path can cancel
            # it by id (it lives in another session). Validated again there.
            try:
                _cd_dup = dict(chat_session.collected_data or {})
                _cd_dup["_dup_booking_id"] = str(_dup_booking.id)
                chat_session.collected_data = _cd_dup
                await crud_chat.update_chat_session(
                    db, chat_session.session_id, chat_session
                )
            except Exception:
                pass
            # NOTE: no transcript write here — every _handle_booking_flow
            # caller persists (user_msg, agent_msg) after return.
            return (
                _dup_msg,
                "REQUIRE_SELECT",
                {
                    "duplicate_booking": True,
                    "booking_id": str(_dup_booking.id),
                    "name": "duplicate_action",
                    "type": "select",
                    "label": "Manage your booking",
                    "options": ["Reschedule booking", "Cancel booking"],
                },
            )
    except Exception as _dup_err:
        logger.warning("duplicate booking guard failed open: %s", _dup_err)

    _verification_revoked_this_turn = False
    if require_otp:
        otp_context_key = _build_otp_context_key(chat_session, fd_map)
        internal_state = _ensure_internal_state(chat_session)
        verified_key = str(internal_state.get("otp_verified_key") or "")
        if chat_session.otp_verified and verified_key != otp_context_key:
            chat_session.otp_verified = False
            internal_state.pop("otp_verified_key", None)
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
            _verification_revoked_this_turn = True

    # A stale verified flag (set with no matching key — e.g. details changed
    # after verification, or a flag with no issuance record) plus a message
    # that is NOT a confirmation must fall back to the review summary: no
    # fresh code («I've verified the code» is a claim about the past, not a
    # new confirmation) and no booking. Without this, the revoked flag sent
    # the flow straight into OTP re-issue and the customer got a code modal
    # for a booking they never confirmed. A genuine code turn is unaffected:
    # its key is stored before this runs, so no revocation fires.
    if (
        _verification_revoked_this_turn
        and user_confirmed is False
        and not (chat_session.missing_fields or [])
    ):
        _stale_summary_msg, _stale_summary_directive = _build_confirmation_summary(
            chat_session, fd_map
        )
        return _stale_summary_msg, _stale_summary_directive, None

    if require_otp and not chat_session.otp_verified:
        from app.crud.otp import generate_otp, get_otp_policy_metadata, invalidate_otp_delivery
        from app.core.mail import send_otp_email
        from app.core.otp_delivery import delivery_error, delivery_succeeded
        from app.core.config import settings as _otp_settings
        otp_send_key = _build_otp_context_key(chat_session, fd_map)
        internal_state = _ensure_internal_state(chat_session)
        if internal_state.get("otp_send_key") == otp_send_key:
            meta = await get_otp_policy_metadata(db, chat_session.session_id, channel="email")
            meta["channel"] = "email"
            return (
                f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. Please enter it here to continue.",
                "REQUIRE_OTP_MODAL",
                meta,
            )

        try:
            if not recipient_email:
                raise ValueError("Email address is required")
            code = await generate_otp(db, chat_session.session_id, channel="email", resend=False)
            _result = await send_otp_email(
                recipient_email,
                code,
                org_id=org.id,
                idempotency_key=otp_send_key,
            )
            if not delivery_succeeded(
                _result,
                allow_mock=bool(getattr(_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
            ):
                await invalidate_otp_delivery(
                    db,
                    chat_session.session_id,
                    "email",
                    context_key=otp_send_key,
                )
                raise RuntimeError(delivery_error(_result))
            internal_state["otp_send_key"] = otp_send_key
            internal_state.pop("otp_verified_key", None)
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
            meta = await get_otp_policy_metadata(db, chat_session.session_id, channel="email")
            meta["channel"] = "email"
            return (
                f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. Please enter it here to continue.",
                "REQUIRE_OTP_MODAL",
                meta,
            )
        except Exception as otp_err:
            logger.error(f"OTP email failed: {otp_err}")
            return (
                "I couldn't send the verification code to your email. Please double-check your email address and try again, or say 'resend' to retry.",
                "REQUIRE_OTP_MODAL",
                {
                    "channel": "email",
                    "validation_error": "We couldn't find an email address to send the code to.",
                },
            )

    # ── Phone OTP gate (sequential after email) ──
    _phone_verified = bool(getattr(chat_session, "phone_otp_verified", False))
    if require_phone_otp:
        _phone = str(chat_session.collected_data.get("phone") or "").strip()
        if not _phone:
            return (
                "I need your phone number to send a verification code. Please share your phone number.",
                "REQUIRE_PHONE_INPUT",
                None,
            )
        _phone_context_key = _build_phone_otp_context_key(chat_session)
        _internal_state = _ensure_internal_state(chat_session)
        _verified_phone_key = str(_internal_state.get("phone_otp_verified_key") or "")
        if _phone_verified and not _verified_key_matches_context(
            _verified_phone_key, _phone_context_key
        ):
            chat_session.phone_otp_verified = False
            _phone_verified = False
            _internal_state.pop("phone_otp_verified_key", None)
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    if require_phone_otp and not _phone_verified:
        from app.crud.otp import generate_otp as _gen_phone_otp, get_otp_policy_metadata as _get_phone_meta, invalidate_otp_delivery as _invalidate_phone_otp
        from app.core.otp_delivery import delivery_error as _phone_delivery_error, delivery_succeeded as _phone_delivery_succeeded
        from app.core.config import settings as _phone_otp_settings
        # Phone OTP via WhatsApp — BYO-OTP generated by the backend
        _phone_send_key = _build_phone_otp_context_key(chat_session)
        _internal_state = _ensure_internal_state(chat_session)
        _phone = str(chat_session.collected_data.get("phone") or "").strip()
        if _internal_state.get("phone_otp_send_key") == _phone_send_key:
            meta = await _get_phone_meta(db, chat_session.session_id, channel="phone")
            meta["channel"] = "phone"
            meta["phone"] = _phone
            via2 = "WhatsApp"
            return (
                f"I've sent a verification code to {_phone} via {via2}. Please enter it here to continue.",
                "REQUIRE_OTP_MODAL",
                meta,
            )
        try:
            code = await _gen_phone_otp(db, chat_session.session_id, channel="phone", resend=False)
            from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider
            _provider = WhatsAppProvider()
            _result = await _provider.send_otp(
                _phone, code, str(chat_session.session_id)
            )
            if not _phone_delivery_succeeded(
                _result,
                allow_mock=bool(getattr(_phone_otp_settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
            ):
                await _invalidate_phone_otp(
                    db,
                    chat_session.session_id,
                    "phone",
                    context_key=_phone_send_key,
                )
                raise RuntimeError(_phone_delivery_error(_result))
            _internal_state["phone_otp_send_key"] = _phone_send_key
            _internal_state.pop("phone_otp_verified_key", None)
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
            meta = await _get_phone_meta(db, chat_session.session_id, channel="phone")
            meta["channel"] = "phone"
            meta["phone"] = _phone
            via = "WhatsApp"
            return (
                f"I've sent a verification code to {_phone} via {via}. Please enter it here to continue.",
                "REQUIRE_OTP_MODAL",
                meta,
            )
        except Exception as otp_err:
            logger.error(f"OTP phone WhatsApp send failed: {otp_err}")
            return (
                "I couldn't send the verification code to your phone. Please double-check your phone number and try again, or say 'resend' to retry.",
                "REQUIRE_OTP_MODAL",
                {
                    "channel": "phone",
                    "validation_error": "We couldn't send the verification code to your phone.",
                },
            )

    # ── Payment gate (provider-aware) ──
    # A Cashfree org is gated on its own credentials; a Razorpay org on
    # Razorpay Route onboarding (TRANSFER_READY is the only allowed state).
    # A booking payment is never requested unless some provider can collect.
    _price_enabled = bool(getattr(org_config, "appointment_price_enabled", False))
    _price_paise = int(getattr(org_config, "appointment_price_paise", 0) or 0)
    _gate_currency = (getattr(org_config, "appointment_currency", "INR") or "INR").upper()
    _payments_ready, _payment_provider = await payment_service.is_usable(
        db, org.id, org_config
    )
    if _price_enabled:
        # Cashfree tenants price per department/member in the catalog.
        # Razorpay orgs keep the flat price (unchanged behavior).
        try:
            _cd0 = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
            _guid0 = getattr(chat_session, "unit_id", None) or _cd0.get("unit_id")
            _gpid0 = getattr(chat_session, "provider_id", None) or _cd0.get("provider_id")
            _price_paise, _gate_currency = await payment_service.resolve_charge(
                db, org.id, org_config, _guid0, _gpid0
            )
        except Exception:
            pass
    _payment_required = _price_enabled and _price_paise > 0 and _payments_ready
    if _price_enabled and _price_paise > 0 and not _payments_ready:
        return (
            "Online payments are temporarily unavailable for this business. Please try again later or contact the business directly.",
            "TEXT_INPUT",
            None,
        )
    if _payment_required:
        from app.models.payment import PaymentAttempt
        from sqlalchemy.future import select as _sel
        # Any paid attempt for this session+org?
        _paid_res = await db.execute(
            _sel(PaymentAttempt).where(
                PaymentAttempt.session_id == chat_session.session_id,
                PaymentAttempt.org_id == org.id,
                PaymentAttempt.status == "paid",
                PaymentAttempt.amount_paise == _price_paise,
            )
        )
        _paid_attempt = _paid_res.scalars().first()
        if not _paid_attempt:
            # Check if there's a created attempt still pending (extend lock window)
            _pending_res = await db.execute(
                _sel(PaymentAttempt).where(
                    PaymentAttempt.session_id == chat_session.session_id,
                    PaymentAttempt.org_id == org.id,
                    PaymentAttempt.status == "created",
                )
            )
            _pending = _pending_res.scalars().first()
            # If pending and not expired, keep payment pending; else request payment
            _currency = _gate_currency
            _meta = {
                "amount_paise": _price_paise,
                "amount_display": f"₹{_price_paise/100:.2f}",
                "currency": _currency,
                "payment_provider": _payment_provider,
                "session_id": str(chat_session.session_id),
            }
            if _pending:
                # Re-surface payment if pending
                return (
                    f"Your appointment total is ₹{_price_paise/100:.2f}. Please complete the payment to confirm your booking.",
                    "REQUIRE_PAYMENT",
                    _meta,
                )
            # No paid attempt → request payment
            return (
                f"Your appointment total is ₹{_price_paise/100:.2f}. Please pay to confirm your booking.",
                "REQUIRE_PAYMENT",
                _meta,
            )

    # OTP verified (or disabled) — proceed to booking
    if chat_session.session_status != "ACTIVE":
        return "Your booking is already processed.", "TEXT_INPUT", None

    if should_create_booking(
        intent=intent,
        user_confirmed=user_confirmed,
        session_status=chat_session.session_status,
        missing_fields=chat_session.missing_fields,
        require_otp=require_otp,
        require_phone_otp=require_phone_otp,
        otp_verified=bool(getattr(chat_session, "otp_verified", False)),
        phone_otp_verified=bool(getattr(chat_session, "phone_otp_verified", False)),
    ):
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

# Reschedule persist pruning: ONLY slot/time + runtime keys are dropped so
# name/email/phone (+dept/doctor/mode) survive a reload. The full
# SESSION_RESET_REMOVE_KEYS includes identity keys — using it on reschedule
# used to erase the customer from the DB row while memory kept going, so
# any reload/rejoin re-asked for all details.
RESCHEDULE_REMOVE_KEYS = (
    "date",
    "time",
    "selected_slot_id",
    "selected_slot_start_utc",
    "locked_slot_id",
    "locked_slot_start_utc",
    "offered_time_slots",
    "_provider_options",
    "_active_path_effects",
    "_otp_policy",
    "_otp_phone_policy",
    "_internal",
    "_cancel_flow",
    "active_directive_id",
    crud_chat.RUNTIME_CHECKPOINT_KEY,
)

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
    transcript_user_text: Optional[str] = None,
) -> ActionPayload:
    """
    Handle user messages AFTER a booking is confirmed, cancelled, or expired.
    Supports: cancel, reschedule, restart new booking, conversational replies.
    """
    _transcript_user_text = (
        transcript_user_text if transcript_user_text is not None else message_text
    )
    user_msg_lower = message_text.lower().strip()
    status = chat_session.session_status

    # Deterministic confirm short-circuit (mirrors the graph runtime): on a
    # finished CONFIRMED booking a confirm word is never a new request.
    if status == "CONFIRMED" and not chat_session.pending_cancel:
        try:
            from app.engine.time_flex import is_pure_confirm_message as _is_confirm_msg_pre

            _pre_confirm = bool(_is_confirm_msg_pre(message_text))
        except Exception:
            _pre_confirm = False
        if _pre_confirm:
            msg = build_finished_session_reply(chat_session, status)
            action = ActionPayload(
                message=msg, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _save_transcript(
                db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
            )
            return action

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
            "Sure, here's the rescheduling procedure:\n"
                "1) Say *'reschedule my booking'*\n"
            "2) I'll reopen your booking flow while keeping your name/email/phone\n"
            "3) You'll choose a new date and time, then confirm.\n\n"
            "No changes have been made to your current booking yet."
        )
        action = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", is_terminal=False
        )
        await _save_transcript(
            db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
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
        return await _reschedule_booking(
            db,
            org,
            chat_session,
            background_tasks,
            user_message=message_text,
        )

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
                    "Great question, your booking is still active. "
                    "To cancel, just say *'Cancel'* and I'll ask for a final yes/no confirmation "
                    "before cancelling. Once confirmed, the slot is released immediately."
                )
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
                )
                return action
            if deny:
                msg = "No worries — your booking stays as is! Let me know if you need anything else. 😊"
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
                )
                return action
            if affirm:
                # OTP-gated: a plain "yes" must NOT delete the booking.
                # Route into the OTP-protected cancel flow (lookup → code → delete).
                from app.engine import cancel_flow as _cancel_flow_mod

                chat_session.pending_cancel = False
                await crud_chat.update_chat_session(
                    db, chat_session.session_id, chat_session
                )
                action = await _cancel_flow_mod.start_cancel_flow(
                    db, chat_session, message_text, org_config=org_config
                )
                await _save_transcript(
                    db, chat_session.session_id, _transcript_user_text, action.message,
                    action.widget_directive or "TEXT_INPUT",
                )
                return action
            if is_question or self_check.get("classification") in {
                "INFO_QUESTION",
                "AMBIGUOUS",
            }:
                msg = (
                    "Your booking is still active. If you want to cancel it, reply *yes*. "
                    "If you only want the steps, say *'show cancellation steps'*."
                )
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
                )
                return action
            else:
                msg = "No worries — your booking stays as is! Let me know if you need anything else. 😊"
                action = ActionPayload(
                    message=msg, widget_directive="TEXT_INPUT", is_terminal=False
                )
                await _save_transcript(
                    db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
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
                "Sure, here's the cancellation procedure:\n"
                "1) Say *'cancel my booking'*\n"
                "2) Enter the email/phone used for the appointment\n"
                "3) Enter the *verification code* I send you. The booking is cancelled only after the code verifies.\n\n"
                "No changes have been made to your current booking."
            )
            action = ActionPayload(
                message=msg, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _save_transcript(
                db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT"
            )
            return action

        if explicit_cancel or soft_cancel or inferred_cancel_action:
            # The confirm screen offers "Cancel to stop": on a complete but
            # unbooked draft that means the DRAFT, never an existing booking.
            from app.engine import cancel_flow as _cancel_flow_mod2

            _aborted = await _cancel_flow_mod2.maybe_abort_unbooked_draft(
                db, chat_session, message_text, org_config=org_config, org=org
            )
            if _aborted is not None:
                await _save_transcript(
                    db, chat_session.session_id, _transcript_user_text, _aborted.message,
                    _aborted.widget_directive or "TEXT_INPUT",
                )
                return _aborted
            # Own confirmed booking → skip the contact lookup, send the
            # cancel OTP straight away (code → cancelled). Anything else
            # keeps the OTP-protected cancel flow (lookup → code → delete).
            _own_b, _own_s = await _cancel_flow_mod2.find_own_upcoming_booking(
                db, org, chat_session
            )
            if _own_b is not None:
                action = await _cancel_flow_mod2.start_cancel_otp_for_booking(
                    db, org, org_config, chat_session, _own_b, _own_s,
                    background_tasks,
                )
            else:
                # OTP-gated cancel: never delete on yes/no. Start the
                # OTP-protected cancel flow (lookup → verification code → delete).
                action = await _cancel_flow_mod2.start_cancel_flow(
                    db, chat_session, message_text, org_config=org_config
                )
            await _save_transcript(
                db, chat_session.session_id, _transcript_user_text, action.message,
                action.widget_directive or "TEXT_INPUT",
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
- If session is CANCELLED/EXPIRED and they want to rebook: Tell them to tap 'Book a new appointment' below.
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
            "CANCELLED": "Your previous booking was cancelled. Tap 'Book a new appointment' below to start a new one!",
            "EXPIRED": "This session expired. Tap 'Book a new appointment' below to start a new booking!",
        }
        msg = fallback.get(status, "Is there anything else I can help you with?")

    from app.engine.user_visible_text import looks_like_confirmation_claim as _looks_claim_pb

    if (
        msg
        and _looks_claim_pb(msg)
        and self_check.get("classification") != "INFO_QUESTION"
    ):
        msg = build_finished_session_reply(chat_session, status)

    if status in ("CANCELLED", "EXPIRED"):
        action = ActionPayload(
            message=msg,
            widget_directive=REBOOK_BUTTON_DIRECTIVE,
            field_meta=rebook_button_field_meta(),
            is_terminal=False,
        )
        await _save_transcript(
            db, chat_session.session_id, _transcript_user_text, msg,
            REBOOK_BUTTON_DIRECTIVE,
        )
        return action
    action = ActionPayload(
        message=msg, widget_directive="TEXT_INPUT", is_terminal=False
    )
    await _save_transcript(db, chat_session.session_id, _transcript_user_text, msg, "TEXT_INPUT")
    return action


_DUP_RESCHEDULE_RE = re.compile(r"\breschedul\w*\b", re.IGNORECASE)


async def maybe_reschedule_dup_booking(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Optional[ActionPayload]:
    """Reschedule the duplicate booking pinned by the dup guard.

    The guard fires in a NEW session while the existing booking lives in an
    old one, so session-scoped reschedule cannot find it. This path cancels
    the pinned booking BY ID (org-scoped, upcoming, contact-owned) and
    re-enters collection with contacts kept — date/time re-picked, OTP at
    the final confirm as usual.
    """
    try:
        cd = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
    except Exception:
        return None
    # Ask-AI sessions never reschedule: booking actions live in the
    # booking section only (the widget enforces this too; belt-and-braces).
    try:
        if str((cd.get("_mode") or "")).strip().lower() == "ask":
            return None
    except Exception:
        pass
    dup_id = str(cd.get("_dup_booking_id") or "").strip()
    if not dup_id:
        return None
    # Own-session CONFIRMED bookings keep their dedicated reschedule path.
    if str(getattr(chat_session, "session_status", "") or "").upper() == "CONFIRMED":
        return None
    if not _DUP_RESCHEDULE_RE.search(str(message_text or "")):
        return None

    async def _drop_pin() -> None:
        try:
            _cd = dict(chat_session.collected_data or {})
            _cd.pop("_dup_booking_id", None)
            chat_session.collected_data = _cd
            await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        except Exception:
            pass

    try:
        import uuid as _uuid

        from sqlalchemy.future import select as _sel

        from app.models.booking import Booking as _Booking
        from app.models.booking import TimeSlot as _Slot

        _bid = _uuid.UUID(dup_id)
        _res = await db.execute(
            select(_Booking).where(_Booking.id == _bid, _Booking.org_id == org.id)
        )
        _booking = _res.scalars().first()
    except Exception:
        _booking = None
    if _booking is None:
        await _drop_pin()
        return None
    # Must still be upcoming.
    try:
        from datetime import datetime
        from datetime import timezone as _tz

        _slot = None
        if getattr(_booking, "slot_id", None):
            _sr = await db.execute(select(_Slot).where(_Slot.id == _booking.slot_id))
            _slot = _sr.scalars().first()
        _start = _slot.start_time if _slot is not None and _slot.start_time else None
        _now = datetime.now(_tz.utc)
        if _start is not None and getattr(_start, "tzinfo", None) is None:
            _start = _start.replace(tzinfo=_tz.utc)
        if not _start or _start <= _now:
            await _drop_pin()
            return None
    except Exception:
        await _drop_pin()
        return None
    # Ownership: session contact must match the booking contact.
    def _digits_match(a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a == b:
            return True
        return len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:]

    try:
        _em = str(cd.get("email") or "").strip().lower()
        _ph = re.sub(r"\D", "", str(cd.get("phone") or ""))
        _bem = str(getattr(_booking, "customer_email", "") or "").strip().lower()
        _bph = re.sub(r"\D", "", str(getattr(_booking, "customer_phone", "") or ""))
        _owned = bool(_em and _em == _bem) or _digits_match(_ph, _bph)
    except Exception:
        _owned = False
    if not _owned:
        await _drop_pin()
        return None

    from app.crud.booking import cancel_booking as _cancel_by_id

    try:
        _cancelled = await _cancel_by_id(db, booking_id=str(_booking.id), org_id=org.id)
    except Exception as _cancel_err:
        logger.warning("dup reschedule cancel failed: %s", _cancel_err)
        _cancelled = False
    if not _cancelled:
        await _drop_pin()
        _fail = (
            "I couldn't cancel your previous booking. Please try again "
            "or contact the business directly."
        )
        try:
            await _save_transcript(
                db, chat_session.session_id, str(message_text or ""), _fail, "TEXT_INPUT"
            )
        except Exception:
            pass
        return ActionPayload(message=_fail, widget_directive="TEXT_INPUT", is_terminal=False)

    customer_name = cd.get("name", "there")
    customer_email = _resolve_session_email(chat_session)
    # Keep contacts, clear only date/time + stale refs (mirrors _reschedule_booking).
    try:
        _cd2 = dict(chat_session.collected_data or {})
        for _k in ("date", "time", "selected_slot_id", "selected_slot_start_utc",
                   "locked_slot_id", "locked_slot_start_utc", "_dup_booking_id"):
            _cd2.pop(_k, None)
        chat_session.collected_data = _cd2
    except Exception:
        pass
    chat_session.session_status = "ACTIVE"
    chat_session.current_intent = "EDIT_DATA"
    chat_session.otp_verified = False
    chat_session.phone_otp_verified = False
    chat_session.missing_fields = ["date", "time"]
    # Persist WITHOUT wiping identity (see RESCHEDULE_REMOVE_KEYS): only
    # slot/time + runtime keys go, so name/email/phone survive a reload.
    from app.engine.cancel_flow import _persist_session

    await _persist_session(db, chat_session, remove_keys=RESCHEDULE_REMOVE_KEYS)
    if background_tasks:
        log_audit_event(
            background_tasks,
            org_id=org.id,
            actor_id=None,
            actor_email=customer_email,
            action=AuditAction.BOOKING_UPDATE,
            resource_type="booking",
            resource_id=str(_booking.id),
            old_values={"booking_id": str(_booking.id)},
            new_values={"session_status": "ACTIVE", "date": None, "time": None, "intent": "EDIT_DATA"},
            ip_address=None,
            user_agent=None,
        )
    msg = (
        f"No problem, {customer_name}! Your previous booking is cancelled. "
        f"Let's pick a new date — when would you like to reschedule to?"
    )
    action = ActionPayload(
        message=msg, widget_directive="REQUIRE_DATE_PICKER", is_terminal=False
    )
    await _save_transcript(
        db,
        chat_session.session_id,
        str(message_text or ""),
        msg,
        "REQUIRE_DATE_PICKER",
        intent="EDIT_DATA",
    )
    return action


async def _reschedule_booking(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    background_tasks: Optional[BackgroundTasks] = None,
    *,
    user_message: str = "reschedule",
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
    chat_session.phone_otp_verified = False
    chat_session.missing_fields = ["date", "time"]
    # Persist WITHOUT wiping identity (see RESCHEDULE_REMOVE_KEYS): only
    # slot/time + runtime keys go, so name/email/phone survive a reload.
    from app.engine.cancel_flow import _persist_session

    await _persist_session(db, chat_session, remove_keys=RESCHEDULE_REMOVE_KEYS)

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
        user_message,
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
    """Cancel a confirmed booking: release the slot and update session status.

    HARD GATE per admin flags: when admin enables email and/or phone OTP,
    real booking deletion requires a verified cancel OTP
    (cancel_flow stage=otp + otp_verified=True). When admin disables both,
    no code is needed. A plain yes/confirm must never delete while OTP is on
    — callers route to start_cancel_flow first.
    """
    from app.crud.booking import cancel_booking_by_session

    _need_any_otp = True  # fail-closed default: OTP on unless proven off
    try:
        from sqlalchemy.future import select as _sel_cfg
        from app.models.config import OrgConfig as _OrgCfg

        _cfg_res = await db.execute(
            _sel_cfg(_OrgCfg).where(_OrgCfg.org_id == org.id)
        )
        _cfg_row = _cfg_res.scalars().first()
        if _cfg_row is not None:
            _r_e = getattr(_cfg_row, "require_otp", True)
            _r_p = getattr(_cfg_row, "require_phone_otp", False)
            _need_any_otp = (True if _r_e is None else bool(_r_e)) or (
                False if _r_p is None else bool(_r_p)
            )
    except Exception:
        _need_any_otp = True
    _flow = (chat_session.collected_data or {}).get("_cancel_flow") or {}
    if _need_any_otp and not (
        _flow.get("stage") == "otp" and _flow.get("otp_verified") is True
    ):
        from app.engine import cancel_flow as _cancel_flow_gate

        action = await _cancel_flow_gate.start_cancel_flow(
            db, chat_session, "cancel my booking", org_config=org_config
        )
        await _save_transcript(
            db, chat_session.session_id, "cancel my booking", action.message,
            action.widget_directive or "TEXT_INPUT",
        )
        return action

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
                    "booking_id": str(existing_booking.id) if existing_booking else "",
                    "name": chat_session.collected_data.get("name"),
                    "email": customer_email,
                    "date": chat_session.collected_data.get("date"),
                    "time": chat_session.collected_data.get("time"),
                    "source_channel": {
                        "web": "Web Widget",
                        "whatsapp": "WhatsApp",
                        "whatsapp_cloud": "WhatsApp",
                    }.get(chat_session.channel, chat_session.channel.title()),
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


def resolve_payment_snapshot_status(
    *,
    price_expected: bool,
    route_ready: bool,
    paid_found: bool,
    lookup_failed: bool = False,
) -> str:
    """Decide the `payment_status` a booking is recorded with.

    Extracted from `_execute_booking` so the decision is testable on its own.
    It used to be inline inside an ~800 line async function guarded by a bare
    `except Exception`, where an UnboundLocalError could quietly resolve to
    "not_required" for every booking at a priced org.

    `route_ready` and `lookup_failed` are deliberately SEPARATE. `route_ready
    = False` is a real business state -- the org is priced but has no
    Razorpay Route account yet, so it genuinely cannot collect payment (the
    widget rejects the order with 409 provider_payout_not_ready). Overloading
    it to also mean "we could not find out" is what made the original silent
    waiver possible, because a crash looked exactly like that state.

    The rule that matters: an UNKNOWN state is never recorded as "nothing
    owed". When the lookup genuinely failed, a priced org is recorded as
    "pending" -- money still expected -- so a failure can never silently
    waive a payment.
    """
    if paid_found:
        return "paid"
    if price_expected and (route_ready or lookup_failed):
        return "pending"
    return "not_required"


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
        # ONLINE_ONLY orgs are online by definition: a collected choice lost
        # upstream (re-extraction, checkpoint merge) must not silently flip
        # the booking to offline or skip link handling.
        if requested_meeting_type is None and mode == MeetingMode.ONLINE_ONLY:
            requested_meeting_type = "ONLINE"

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
        # The link factory generates via the ACTIVE provider (Meet/Teams/Zoom
        # connection), so a connected active provider IS an online path even
        # when no calendar is linked and no default provider is set.
        from app.models.config import ActiveMeetingProvider as _AMP

        _active_provider = (
            live_policy.active_meeting_provider
            if live_policy and getattr(live_policy, "active_meeting_provider", None)
            else None
        )
        has_online_path = (
            bool(live_policy and live_policy.google_calendar_connected)
            or bool(live_policy and live_policy.outlook_calendar_connected)
            or active_provider != MeetingProviderDefault.NONE
            or (_active_provider is not None and _active_provider != _AMP.NONE)
        )
        # Degraded confirmation (product contract): an ONLINE pick without
        # any connected online path must NOT deadlock the booking into a
        # confirm → summary → confirm loop. The booking goes through WITHOUT
        # the link (UNAVAILABLE + ops alert); the customer is told the link
        # follows by email (D-03 message below).
        _no_online_path = is_online_request and not has_online_path
        if _no_online_path:
            logger.warning(
                "booking.online_path_missing.degraded",
                extra={
                    "session_id": chat_session.session_id,
                    "meeting_type": requested_meeting_type,
                },
            )
            try:
                request_id = (
                    correlation_id.get()
                    or (chat_session.collected_data or {}).get("request_id")
                )
                trace_id = (chat_session.collected_data or {}).get("trace_id")
                await create_system_alert(
                    db,
                    SystemAlertCreate(
                        org_id=org.id,
                        component=AlertComponent.BOOKING,
                        severity=AlertSeverity.WARNING,
                        code="ONLINE_MEETING_PATH_MISSING",
                        message="Online booking confirmed without a meeting link: no online path connected",
                        request_id=request_id,
                        trace_id=trace_id,
                        context={"meeting_type": requested_meeting_type},
                    ),
                )
            except Exception as alert_err:
                logger.warning(
                    "booking.online_path_missing.alert.failed: %s", alert_err
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

        # No connected online path: mark degraded up front so the link
        # generation branches below are skipped (calling the provider
        # factory with nothing configured raises → summary loop).
        if _no_online_path:
            meeting_link_status = MeetingLinkStatus.UNAVAILABLE
            meeting_link_error_code = "NO_ONLINE_PATH"
            meeting_link_degraded = True

        # Determine if this is an ONLINE booking that requires link generation
        is_online_only_mode = mode == MeetingMode.ONLINE_ONLY
        user_chose_online = requested_meeting_type == "ONLINE"
        configured_active_provider = (
            live_policy.active_meeting_provider
            if live_policy and getattr(live_policy, "active_meeting_provider", None)
            else None
        )

        if _no_online_path and (is_online_only_mode or user_chose_online):
            # Customer chose online: snapshot stays ONLINE, link pending.
            meeting_type_snapshot = "ONLINE"
        if is_online_only_mode and not _no_online_path:
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
        elif user_chose_online and mode == MeetingMode.HYBRID and not _no_online_path:
            # HYBRID ONLINE: generate link via active provider (same as ONLINE_ONLY)
            meeting_type_snapshot = "ONLINE"
            link_result = await generate_meeting_link(
                config=live_policy,
                db=db,
                booking_id=str(target_slot.id),
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
                meeting_link_status = MeetingLinkStatus.UNAVAILABLE
                meeting_link_error_code = (
                    link_result.error_code.value if link_result.error_code else None
                )
                meeting_link_degraded = True
                logger.warning(
                    f"Meeting link generation failed (degraded) HYBRID: {link_result.error_message}",
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
                            code="HYBRID_MEETING_LINK_UNAVAILABLE",
                            message="Meeting link generation unavailable during HYBRID ONLINE confirmation",
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
                        "booking.meeting_link.degraded.alert.failed HYBRID: %s", alert_err
                    )
        elif not _no_online_path:
            # OFFLINE or HYBRID with OFFLINE selection
            meeting_type_snapshot = "OFFLINE"
            meeting_link_status = MeetingLinkStatus.NOT_REQUIRED
        # else (_no_online_path): degraded UNAVAILABLE set above is kept.

        # Payment snapshot from latest paid attempt (if any)
        #
        # `org_config` is loaded HERE, before this block, because it used to be
        # read ~150 lines further down at the Google Calendar step. Since
        # `_execute_booking` does not take org_config as a parameter and this
        # function assigns it later, it is a LOCAL variable for the whole
        # function -- so the earlier read raised UnboundLocalError, which the
        # `except Exception` below swallowed into
        # `_pay_status = "not_required"`. The net effect: for an org with
        # pricing enabled, every booking was stamped not_required regardless
        # of what was actually paid.
        _cfg_result = await db.execute(
            select(OrgConfig).where(OrgConfig.org_id == org.id)
        )
        org_config = _cfg_result.scalars().first()
        _pay_order_id = None
        _pay_payment_id = None
        _pay_amount = None
        _pay_currency = None
        _pay_status = "not_required"
        _paid3 = None
        # Whether the admin has this org priced. Used both to decide whether a
        # payment is expected at all and, on failure, to avoid defaulting to
        # "not_required" for an org that DOES require payment.
        _price_expected = False
        try:
            _price_enabled3 = bool(getattr(org_config, "appointment_price_enabled", False))
            _price_paise3 = int(getattr(org_config, "appointment_price_paise", 0) or 0)
            _price_expected = bool(_price_enabled3 and _price_paise3 > 0)
            # Provider-aware collection readiness: Razorpay orgs need Route
            # TRANSFER_READY; Cashfree orgs need usable keys (own or platform).
            _payments_ready3, _pay_provider3 = await payment_service.is_usable(
                db, org.id, org_config
            )
            _paid3 = None  # (initialized to None above; reset per booking attempt)
            if _price_expected and _payments_ready3:
                from app.models.payment import PaymentAttempt as _PA3
                from sqlalchemy.future import select as _sel3
                _paid3 = (await db.execute(_sel3(_PA3).where(_PA3.session_id == chat_session.session_id, _PA3.org_id == org.id, _PA3.status == "paid"))).scalars().first()
            if _paid3:
                _paid3_provider = (getattr(_paid3, "provider", None) or "razorpay").lower()
                if _paid3_provider == "cashfree":
                    # Booking carries Razorpay-only reference columns; a
                    # Cashfree order id must never be stored in them. The
                    # attempt->booking link below keeps full traceability.
                    _pay_order_id = None
                    _pay_payment_id = None
                else:
                    _pay_order_id = _paid3.razorpay_order_id or getattr(_paid3, "provider_order_id", None)
                    _pay_payment_id = _paid3.razorpay_payment_id or getattr(_paid3, "provider_payment_id", None)
                _pay_amount = _paid3.amount_paise
                _pay_currency = _paid3.currency
            _pay_status = resolve_payment_snapshot_status(
                price_expected=_price_expected,
                route_ready=_payments_ready3,
                paid_found=bool(_paid3),
            )
        except Exception:
            # An unknown payment state must never be recorded as "nothing owed".
            # `route_ready=False` is the honest representation of "we could not
            # find out", and the resolver turns that into "pending" whenever the
            # org is priced, rather than silently waiving the payment.
            _pay_status = resolve_payment_snapshot_status(
                price_expected=_price_expected,
                route_ready=False,
                paid_found=False,
                lookup_failed=True,
            )
            if _price_expected:
                logger.error(
                    "payment snapshot lookup failed for a priced org; "
                    "recording payment_status=pending rather than not_required",
                    exc_info=True,
                )
            else:
                logger.warning("payment snapshot lookup failed", exc_info=True)

        # Service catalog linkage (Phase 3): IDs arrive via collected_data.
        # Invalid/stale refs are dropped with a warning — a confirmed booking
        # must never fail on catalog data, and never persist foreign refs.
        _sel_unit_id = None
        _sel_provider_id = None
        try:
            from app.services.catalog_service import validate_catalog_selection as _vcs

            _cd = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
            _vu, _vp = await _vcs(
                db, org.id, _cd.get("unit_id"), _cd.get("provider_id")
            )
            _sel_unit_id = str(_vu.id) if _vu is not None else None
            _sel_provider_id = str(_vp.id) if _vp is not None else None
        except ValueError as _cat_err:
            logger.warning("catalog selection dropped at booking: %s", _cat_err)
        except Exception as _cat_err2:
            logger.warning("catalog selection check failed at booking: %s", _cat_err2)

        # Catalog fee fills the amount only when no Razorpay-paid amount exists.
        if _pay_amount is None:
            try:
                from app.services.catalog_service import resolve_effective_fee as _ref

                _cd2 = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
                _fee_paise, _fee_cur = await _ref(
                    db, org.id, _cd2.get("unit_id"), _cd2.get("provider_id")
                )
                if _fee_paise:
                    _pay_amount = _fee_paise
                    _pay_currency = _fee_cur
            except Exception as _fee_err:
                logger.warning("catalog fee resolution failed at booking: %s", _fee_err)

        booking = await attempt_booking(
            db=db,
            session_id=chat_session.session_id,
            org_id=str(org.id),
            slot_id=str(target_slot.id),
            customer_name=chat_session.collected_data.get("name"),
            customer_email=customer_email,
            customer_phone=chat_session.collected_data.get("phone"),
            unit_id=_sel_unit_id,
            provider_id=_sel_provider_id,
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
            # Payment snapshot
            payment_status=_pay_status,
            razorpay_order_id=_pay_order_id,
            razorpay_payment_id=_pay_payment_id,
            amount_paise=_pay_amount,
            currency=_pay_currency,
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

        # Link the paid attempt to this booking (any provider). Gives full
        # payment->booking traceability in Recent transactions.
        if _paid3 is not None and not getattr(_paid3, "booking_id", None):
            try:
                _paid3.booking_id = booking.id
                await db.flush()
            except Exception:
                logger.warning("link paid attempt to booking failed", exc_info=True)

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
        # `org_config` was loaded above, before the payment snapshot. It used to
        # be fetched here, which meant every earlier read of it raised
        # UnboundLocalError.
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
                
            )

        # Add source channel to booking details for emails
        booking_details["source_channel"] = {
            "web": "Web Widget",
            "whatsapp": "WhatsApp",
            "whatsapp_cloud": "WhatsApp",
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

        # Department/member/fee lines for the confirmation email.
        try:
            from app.services.catalog_service import booking_email_context

            booking_details.update(await booking_email_context(db, booking))
        except Exception:
            pass

        from app.models.user import User
        from sqlalchemy.future import select as sa_select

        admin_result = await db.execute(sa_select(User).where(User.org_id == org.id))
        admin_user = admin_result.scalars().first()
        admin_email = admin_user.email if admin_user else None

        email_phrase = ""
        if dispatch_notifications:
            from app.core.mail import send_confirmation_emails

            # Explicit: always send confirmation email if email present, even when email OTP was disabled
            # and booking was verified via phone OTP only. Email OTP toggle controls verification, not confirmation.
            if customer_email:
                logger.info(
                    "Booking confirmation email queued for %s via phone-otp flow (require_otp=%s, dispatched via %s)",
                    customer_email,
                    str(getattr(chat_session, 'otp_verified', 'n/a')),
                    "background_tasks" if background_tasks else "await",
                )
            email_delivery_failed = False
            if customer_email:
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
        # D-03: Customer-facing degraded link message (hide internal dashboard details)
        if meeting_link_degraded:
            return (
                "🎉 Your booking is confirmed! The meeting link will be shared via email shortly. "
                "Your reservation is saved."
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
            allow_existing_lock_id=chat_session.collected_data.get("locked_slot_id"),
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
        summary_lines.append(f"{icon} *{label}*: {display_val}")
    if not summary_lines:
        summary_lines.append("📋 *Details*: Please review the details above before confirming.")
    summary_text = (
        "Perfect! Let's double check your details:\n\n"
        + "\n".join(summary_lines)
        + "\n\nShall I go ahead and book this for you?"
    )
    return summary_text, "REQUEST_CONFIRMATION"


def _is_unverified_booking_claim(chat_session) -> bool:
    """True when the session already has a real confirmed booking.

    Guards the edit-re-summary: if a genuine booking exists, an edit request
    is a reschedule and must be handled by the booking flow, not by showing a
    pre-confirmation summary for an appointment that is already booked.
    """
    return str(getattr(chat_session, "session_status", "") or "").upper() in (
        "CONFIRMING",
        "CONFIRMED",
    )


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
        summary.append(f"{icon} *{label}:* {display_val}{mark}")
    if not summary:
        summary.append("📋 *Details:* No editable fields collected yet.")
    return (
        f"Got it, I've updated that for you! Here's your revised summary:\n\n"
        + "\n".join(summary)
        + "\n\nReply CONFIRM and I'll send a fresh verification code to lock in the change.",
        "REQUEST_CONFIRMATION",
    )


def _build_confirmation_summary(
    chat_session: ChatSessionState, fd_map: Dict[str, FieldDefinition]
) -> Tuple[str, str]:
    """Build the pre-booking review block.

    Layout rules (applied again by app.core.wa_text.format_outbound on the way
    out, so this stays the single place that decides structure):
      * one blank line between blocks so the message reads in sections
      * a bolded title line, never a bare ALL-CAPS header (those leaked from
        prompt scaffolding into customer replies)
      * label bolded, value plain, so the eye lands on the label first
      * date and time carry the most weight in a booking, so they are
        emphasised in the closing line too
    """
    summary = []
    for key, val, fd in _iter_user_facing_summary_fields(chat_session, fd_map):
        icon = fd.icon if fd else "\U0001F4CB"
        label = fd.label if fd else key.replace("_", " ").title()
        display_val = _format_display_value(key, val, fd)
        summary.append(f"{icon} *{label}:* {display_val}")
    if not summary:
        return (
            "\U0001F4CB *Booking details*\n\n"
            "I don't have all the details yet.\n\n"
            "Could you please share your preferred date and time?",
            "REQUEST_CONFIRMATION",
        )

    # Review-presented marker: the OTP/booking gate requires a review card
    # to have gone out before PROCESS_BOOKING. Set here — the single choke
    # point every review passes through — so a turn that jumps collection
    # straight to confirm/OTP is rerouted to the card instead. Internal key
    # (never rendered: the iterator above only yields fd_map fields).
    # Callers persist collected_data through their normal session saves.
    try:
        if isinstance(chat_session.collected_data, dict):
            chat_session.collected_data["_review_presented"] = True
    except Exception:
        pass
    date_value = str((chat_session.collected_data or {}).get("date") or "").strip()
    time_value = str((chat_session.collected_data or {}).get("time") or "").strip()
    when_bits = [b for b in (date_value, time_value) if b]

    lines = ["\U0001F4C5 *Please review your booking*", ""]
    lines.extend(summary)
    if when_bits:
        lines.extend(["", "\U0001F552 *When:* " + " at ".join(when_bits)])
    lines.extend(
        [
            "",
            "Tap *Confirm* to book, *Edit* to change something, or *Cancel* to stop.",
        ]
    )
    return ("\n".join(lines), "REQUEST_CONFIRMATION")


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


# Fields that make a session "already started" for opening-greeting purposes.
_OPENING_SESSION_FIELDS = ("name", "email", "phone", "date", "time")


def is_opening_turn(chat_session: ChatSessionState) -> bool:
    """True when the customer has not given us anything yet.

    Cheap and deterministic: if none of the collected fields have a value,
    this is the first real message of a new conversation, which is the only
    moment a salutation is appropriate. A false positive would mean greeting
    a customer mid-flow, so nothing else may be used as the trigger.
    """
    collected = chat_session.collected_data
    if not isinstance(collected, dict):
        return True
    for key in _OPENING_SESSION_FIELDS:
        value = collected.get(key)
        if value not in (None, "", [], {}):
            return False
    return True


def build_opening_greeting(
    name: str, business_name: str, next_question: str
) -> str:
    """A salutation that acknowledges what the customer actually sent.

    Deliberately built here rather than asked of the model: the greeting has
    to use the name that was just captured, and a model asked to greet a
    named stranger will sometimes greet them by the wrong name.
    """
    who = f" {str(name).strip()}" if str(name or "").strip() else ""
    biz = str(business_name or "").strip()
    biz_part = f" to {biz}" if biz else ""
    return (
        f"Hello{who}! Thanks for reaching out{biz_part}.{next_question}"
    )


async def maybe_greet_opening_name(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    field_definitions: Optional[List[FieldDefinition]] = None,
    inbound_message_id: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Optional[ActionPayload]:
    """Handle "the customer's first message is their name".

    Reported behaviour: a customer opened with "Hi Yash" and was told "Hello!
    Could you please share your full name?" -- the name they had just given
    was ignored and they were asked for it again.

    Here the name is captured, acknowledged by name, and the customer is
    moved on to the next missing field. Returns None for every other opening
    (a greeting alone, a question, gibberish, an email, a phone number), so
    the normal engine handles those unchanged.
    """
    if not is_opening_turn(chat_session):
        return None

    fd_list = list(field_definitions or [])
    fd_map = {fd.name: fd for fd in fd_list}

    # Only intervene when the name is the FIRST thing we still need. If the
    # field order were different this must not fire.
    missing = list(chat_session.missing_fields or [])
    if missing and missing[0] != "name":
        return None
    if not missing and "name" in (
        [fd.name for fd in fd_list] or ["name", "email", "phone", "date", "time"]
    ):
        # Name is not required by this org's config: nothing to acknowledge.
        return None

    candidate = extract_opening_name(message_text)
    if not candidate:
        return None
    # The opener extractor is syntactic (shape of a name); the validator is
    # the semantic gate. Without it a day-word opening ("yesterday") was
    # persisted as the customer's name. Reuses the same name validator as
    # every other path — no separate rules here.
    _parsed_candidate, _candidate_err = DataValidator._validate_name(candidate)
    if _candidate_err or not _parsed_candidate:
        return None
    candidate = _parsed_candidate

    collected = dict(chat_session.collected_data or {})
    collected["name"] = candidate
    chat_session.collected_data = collected

    still_missing = StateEvaluator.calculate_missing_fields(
        get_required_field_names(fd_list), collected
    )
    chat_session.missing_fields = still_missing
    chat_session.current_intent = "PROVIDE_DATA"
    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    next_question = ""
    if still_missing:
        _next = fd_map.get(still_missing[0])
        _label = str(getattr(_next, "label", "") or still_missing[0]).strip()
        if still_missing[0] == "date":
            next_question = " What date would you like to book?"
        elif still_missing[0] == "time":
            next_question = " What time works for you?"
        else:
            next_question = f" Could you please share your {_label.lower()}?"
    else:
        next_question = " How can we help you today?"

    business = str(getattr(org_config, "business_name", "") or "").strip() or str(
        getattr(org, "name", "") or ""
    ).strip()
    message = build_opening_greeting(candidate, business, next_question)

    action = ActionPayload(
        message=message,
        widget_directive="TEXT_INPUT",
        field_meta={"name": "name", "type": "text", "opening_greeting": True},
    )
    try:
        await _save_transcript(
            db,
            chat_session.session_id,
            str(message_text or ""),
            message,
            directive="TEXT_INPUT",
            intent="PROVIDE_DATA",
        )
    except Exception:
        logger.debug("opening-greeting transcript save failed")
    return action
