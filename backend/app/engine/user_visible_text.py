import json
import re
from typing import Any, Dict, Tuple


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*? response", flags=re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", flags=re.IGNORECASE)


CONFIRMATION_CLAIM_PATTERN = re.compile(
    r"\b("
    r"(?:booking|appointment|reservation|slot)\s+"
    r"(?:(?:has\s+been\s+|was\s+|is\s+)(?:now\s+|successfully\s+)?|(?:now\s+|successfully\s+))"
    r"(?:confirmed|booked|reserved|scheduled|finalized)|"
    r"\b(?:your|the|a)\s+(?:booking|appointment|reservation|slot)\s+"
    r"(?:(?:has\s+been|was|is)\s+)?(?:now\s+|successfully\s+)?"
    r"(?:confirmed|booked|reserved|scheduled|finalized)|"
    r"(?:booking|appointment|reservation)\s+(?:is\s+)?confirmed|"
    r"successfully\s+(?:booked|confirmed|reserved|scheduled)|"
    r"(?:booked|confirmed|reserved|scheduled)\s+successfully|"
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
    text = _THINK_TAG_RE.sub("", text)
    return text.strip() or "I'm here to help with your booking."


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

    if looked_like_json_object:
        return sanitize_user_visible_text(raw_message), parsed_payload
    return sanitize_user_visible_text(raw_message), parsed_payload