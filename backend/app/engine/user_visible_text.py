import json
import re
from typing import Any, Dict, Tuple


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", flags=re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", flags=re.IGNORECASE)
_REASONING_LEAK_RE = re.compile(
    r"(?:here\s+is\s+my\s+analysis|let's\s+reason|reason\s+step\s+by\s+step|"
    r"internal\s+reasoning|chain\s+of\s+thought|first\s+i['’]?ll\s+reason|"
    r"before\s+giving\s+(?:a\s+)?final\s+answer|final\s+answer\s*:)",
    flags=re.IGNORECASE,
)
_FALLBACK_TEXT = "I'm here to help with your booking."

# Structural keys of our LLM contract. Their presence means "this was meant
# to be a machine payload" — never business content, safe to key on.
_RESPONSE_KEYS = (
    "response_text",
    "response_message",
    "message",
    "text",
    "assistant_response",
)
_CONTRACT_KEYS = ("response_text", "extracted_fields", "user_intent")

_RESPONSE_VALUE_RE = re.compile(
    r'"(response_text|response_message|message|text|assistant_response)"'
    r'\s*:\s*"((?:[^"\\]|\\.)*)"',
    flags=re.DOTALL,
)


def looks_like_json_debris(text: Any) -> bool:
    """True when text carries our LLM-contract keys but is not valid JSON.

    Rendered verbatim this leaks internals ("response_text": ... bubbles),
    so callers must substitute a safe fallback instead.
    """
    lowered = str(text or "").lower()
    return any(f'"{key}"' in lowered for key in _CONTRACT_KEYS)


def _repair_response_text(text: str) -> str:
    """Salvage the human reply from malformed model JSON (missing braces,
    leading/trailing prose the balanced-span parser missed)."""
    stripped = str(text or "").strip()
    if not stripped or '"' not in stripped:
        return ""
    match = _RESPONSE_VALUE_RE.search(stripped)
    if not match:
        return ""
    raw_value = match.group(2)
    try:
        decoded = json.loads(f'"{raw_value}"')
        if isinstance(decoded, str) and decoded.strip():
            return decoded
    except Exception:
        pass
    fallback = (
        raw_value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\").strip()
    )
    return fallback


CONFIRMATION_CLAIM_PATTERN = re.compile(
    r"\b("
    r"(?:booking|appointment|reservation|slot)\s+"
    r"(?:(?:has\s+been\s+|was\s+|is\s+)(?:now\s+|successfully\s+)?|(?:now\s+|successfully\s+))"
    r"(?:confirmed|booked|reserved|scheduled|finalized)|"
    r"\b(?:your|the|a)\s+(?:booking|appointment|reservation|slot)\s+"
    r"(?:(?:has\s+been|was|is)\s+)?(?:now\s+|successfully\s+)?"
    r"(?:confirmed|booked|reserved|scheduled|finalized)|"
    r"(?:booking|appointment|reservation)\s+(?:is\s+)?confirmed|"
    # A date/slot detail wedged between the noun and the verb used to defeat
    # the whole truthfulness guard:
    #     "Your booking for September 30, 2026 at 10:30 AM is confirmed."
    # scored False, so the model told a customer they were booked while no
    # Booking row existed. Allow a bounded detail clause between the noun and
    # the verb. Commas are allowed (dates read "September 30, 2026") but the
    # clause still stops at a period, semicolon or full stop, so it can never
    # span into the next sentence.
    r"(?:booking|appointment|reservation|slot)\s+"
    r"(?:(?:for|on|at|to)\s+[^.;!?]{0,60}?)?"
    r"(?:(?:has\s+been|have\s+been|was|were|is|are)\s+)?"
    r"(?:still\s+|now\s+|already\s+|successfully\s+)*"
    r"(?:confirmed|booked|reserved|scheduled|finalized)\b|"
    # "your appointment is set for ..." asserts completion just as strongly as
    # "confirmed" and previously slipped through, so a customer was told the
    # slot was taken with no Booking row ever written. Only the booking nouns
    # qualify, so mid-flow phrasing like "the date is set" is not affected.
    r"(?:booking|appointment|reservation|slot)\s+(?:is\s+|are\s+|has\s+been\s+|have\s+been\s+|was\s+|were\s+)?(?:set|locked\s+in)\b|"
    r"(?:we|i)\s+(?:have\s+|has\s+)?(?:booked|confirmed|reserved|scheduled)\s+(?:your|the)\b|"
    r"successfully\s+(?:booked|confirmed|reserved|scheduled)|"
    r"(?:booked|confirmed|reserved|scheduled)\s+successfully|"
    # "All confirmed!", "All set, you're booked", "That's confirmed" — short
    # affirmative confirmations that carry no noun, so the patterns above
    # missed them and they reached customers before the booking existed.
    r"\ball\s+(?:set|confirmed|booked|done)\b|"
    r"that'?s\s+(?:confirmed|booked|reserved)\b|"
    r"(?:you'?re|you\s+are)\s+(?:all\s+)?(?:confirmed|booked|reserved)\b|"
    r"\bdone!\s*(?:your|.)?\s*(?:booking|appointment|slot)"
    r")\b",
    flags=re.IGNORECASE,
)
CONFIRMATION_NEGATION_PATTERN = re.compile(
    r"\b(?:not|no|never|cannot|can't|isn't|wasn't|hasn't|haven't|won't)\s+"
    r"(?:confirmed|booked|reserved|scheduled|finalized|locked)",
    flags=re.IGNORECASE,
)


def looks_like_confirmation_claim(message: str) -> bool:
    """True when the message asserts a booking/appointment was completed.

    Question forms ("is your slot booked?"), negations ("your slot is not
    booked") and requests ("please confirm the booking") never count.
    """
    text = message or ""
    if CONFIRMATION_NEGATION_PATTERN.search(text):
        return False
    match = CONFIRMATION_CLAIM_PATTERN.search(text)
    if not match:
        return False
    sentence_start = max(
        text.rfind(".", 0, match.start()),
        text.rfind("!", 0, match.start()),
        text.rfind("?", 0, match.start()),
    ) + 1
    sentence = text[sentence_start:].strip()
    if sentence.endswith("?"):
        return False
    prefix = text[max(0, match.start() - 30) : match.start()].lower()
    if re.search(
        r"\b(?:not|no|never|unable|isn't|wasn't|hasn't|haven't|didn't|cannot|can't)\b",
        prefix,
    ):
        return False
    return True


_SAVED_CLAIM_VERB_PATTERN = re.compile(
    r"\b(?:saved|updated|recorded|stored|noted(?: down)?|added|set|taken)\b",
    flags=re.IGNORECASE,
)


def looks_like_field_saved_claim(message: str, field_name: str) -> bool:
    """True when the message asserts a user-supplied field value was saved.

    Used to detect LLM claims that contradict a rejected/invalid value
    (e.g. "I've saved your phone number" or "Your phone number is 1234567890"
    while the phone failed validation).
    """
    text = message or ""
    if _SAVED_CLAIM_VERB_PATTERN.search(text):
        return True
    field = (field_name or "").lower().strip()
    if field and re.search(
        rf"\byour\s+{re.escape(field)}"
        rf"(?:\s+(?:number|address|value|details))?\s+is\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def sanitize_user_visible_text(raw_message: Any) -> str:
    text = str(raw_message or "")
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_TAG_RE.sub("", text).strip()
    if not text or _REASONING_LEAK_RE.search(text):
        return _FALLBACK_TEXT
    return text


def _extract_embedded_json_object(text: str) -> Dict[str, Any]:
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : index + 1]
                try:
                    decoded = json.loads(snippet)
                except Exception:
                    return {}
                return decoded if isinstance(decoded, dict) else {}
    return {}


def extract_user_visible_text(raw_message: Any) -> Tuple[str, Dict[str, Any]]:
    parsed_payload: Dict[str, Any] = {}
    looked_like_json_object = False

    if isinstance(raw_message, dict):
        parsed_payload = dict(raw_message)
    elif isinstance(raw_message, str):
        stripped = raw_message.strip()
        candidate = stripped
        if "```json" in stripped:
            try:
                candidate = stripped.split("```json", 1)[1].split("```", 1)[0].strip()
            except Exception:
                candidate = stripped
        looked_like_json_object = candidate.startswith("{") and candidate.endswith("}")
        if looked_like_json_object:
            try:
                decoded = json.loads(candidate)
                if isinstance(decoded, dict):
                    parsed_payload = decoded
            except Exception:
                parsed_payload = {}
        else:
            embedded = _extract_embedded_json_object(stripped)
            if isinstance(embedded, dict) and embedded:
                parsed_payload = embedded
                looked_like_json_object = True

    if parsed_payload:
        for key in ("response_text", "response_message", "message", "text", "assistant_response"):
            value = parsed_payload.get(key)
            if isinstance(value, str) and value.strip():
                return sanitize_user_visible_text(value), parsed_payload
        if looked_like_json_object:
            return sanitize_user_visible_text(raw_message), parsed_payload

    # Malformed payload (e.g. missing opening brace): salvage the reply.
    if not parsed_payload and isinstance(raw_message, str):
        repaired = _repair_response_text(raw_message)
        if repaired:
            return (
                sanitize_user_visible_text(repaired),
                {"response_text": repaired, "_repaired": True},
            )

    # Unrecoverable machine debris must never render verbatim.
    if looks_like_json_debris(raw_message):
        return sanitize_user_visible_text(""), {}

    if looked_like_json_object:
        return sanitize_user_visible_text(raw_message), parsed_payload
    return sanitize_user_visible_text(raw_message), parsed_payload