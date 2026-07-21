import json
import re
from typing import Any, Dict, Tuple


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", flags=re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", flags=re.IGNORECASE)
_REASONING_LEAK_RE = re.compile(
    r"(?:\bchain[-\s]?of[-\s]?thought\b|\blet'?s\s+reason\b|\blet'?s\s+think\b|"
    r"\binternal\s+reasoning\b|\banalysis\s*:|\breasoning\s*:|\bthought\s*:|\bstep\s+by\s+step\b)",
    flags=re.IGNORECASE,
)
_BOOKING_SAFE_RE = re.compile(
    r"\b(book|booking|appointment|date|time|slot|otp|email|phone|confirm|reschedule)\b",
    flags=re.IGNORECASE,
)


def _looks_like_reasoning_leak(text: str) -> bool:
    if not text:
        return False
    if not _REASONING_LEAK_RE.search(text):
        return False
    return not _BOOKING_SAFE_RE.search(text)


def sanitize_user_visible_text(raw_message: Any) -> str:
    text = str(raw_message or "")
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_TAG_RE.sub("", text)
    text = text.strip()
    if _looks_like_reasoning_leak(text):
        return "I'm here to help with your booking."
    return text or "I'm here to help with your booking."


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
            return "I'm here to help with your booking.", parsed_payload

    if looked_like_json_object:
        return "I'm here to help with your booking.", parsed_payload
    return sanitize_user_visible_text(raw_message), parsed_payload
