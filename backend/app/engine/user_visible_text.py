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

# A booking redirect glued onto the end of an answer. Production symptoms:
#   "The capital of Japan is Tokyo. Back to your booking, could you please
#    share a preferred date?"
#   "I don't have information about that. How about we finish up your
#    booking instead?"
#   "I can help you book an appointment though. Would you like to pick a
#    time for October 8th?"
# The answer arrives, and is immediately buried under a push. Asking one
# question should not read as an attempt to resume the booking. Kept here
# (not in the orchestrator) so both the engine and the transport combiner
# use the same patterns.
_BOOKING_REDIRECT_SENTENCE_RE = re.compile(
    r"(?:(?<=^)|(?<=[.!?]))\s*"
    r"(?:"
    r"(?:back|getting)\s+to\s+(?:your|the)\s+booking"
    r"|for\s+your\s+booking"
    r"|(?:continue|proceed|resume|finish\s+up)\s+(?:with\s+)?(?:the\s+|your\s+)?booking"
    r"|(?:would|do)\s+you\s+like\s+to\s+(?:book|schedule|continue|proceed|pick)"
    r"|how\s+about\s+we\s+(?:finish\s+up|continue|proceed|book)"
    r"|i\s+can\s+help\s+you\s+book\b"
    r"|(?:let'?s|shall\s+we)\s+(?:get|set)\s+(?:your\s+\w+\s+)?(?:set\s+up|going|booked)"
    r"|which\s+date\s+works\s+for\s+you"
    r"|do\s+you\s+want\s+to\s+(?:book|schedule|proceed)"
    r"|is\s+there\s+anything\s+i\s+can\s+help\s+you\s+book\b"
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


def strip_booking_redirect(text: str) -> str:
    """Remove a "back to your booking" push from an answer.

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
    # Date/time subjects with an explicit completion adverb ("that date is
    # ALREADY confirmed") assert completion just as strongly — production
    # sent exactly that with no Booking row. The adverb is mandatory so
    # mid-flow phrasing ("your date is confirmed?" as a question, "the date
    # is set") stays unaffected: questions are excluded below and bare
    # "date is confirmed" without an adverb does not match.
    r"(?:date|time)\s+(?:is\s+|was\s+)?(?:already|still|successfully|now)\s+"
    r"(?:confirmed|booked|reserved|scheduled|finalized)\b|"
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
    r"\b(?:saved|updated|recorded|stored|noted(?: down)?|added|set|taken"
    r"|received|collected)\b",
    flags=re.IGNORECASE,
)

# Receipt-shaped phrasing the verb list misses. Production sent
# "Thanks! I've got your number. When would you like to book..." while the
# phone had FAILED validation and was never saved — the customer believed
# the number was done and the bot asked for it forever. Bare "have" is
# deliberately excluded ("I have sent the OTP", "I have a question" are not
# receipt claims).
_GOT_YOUR_FIELD_RE = re.compile(
    r"\b(?:i'?ve?\s+got|i\s+got|got\s+it|got\s+your|have\s+your"
    r"|received\s+your|collected\s+your)\b",
    flags=re.IGNORECASE,
)


def looks_like_field_saved_claim(message: str, field_name: str) -> bool:
    """True when the message asserts a user-supplied field value was saved.

    Used to detect LLM claims that contradict a rejected/invalid value
    (e.g. "I've saved your phone number", "Thanks! I've got your number",
    or "Your phone number is 1234567890" while the phone failed validation).
    """
    text = message or ""
    if _SAVED_CLAIM_VERB_PATTERN.search(text):
        return True
    if _GOT_YOUR_FIELD_RE.search(text):
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


# Instruction-shaped phrasing from our own prompt scaffolding. When the
# model echoes these into a reply they must be cut out sentence-wise while
# the human remainder survives. Topic words alone ("business hours",
# "availability") are deliberately NOT matched — only instruction phrasing
# a receptionist would never say to a customer.
_INSTRUCTION_ECHO_RES = (
    # The production leak (scheduling_policy BUSINESS HOURS block) and its
    # paraphrases: "openings hours", dropped commas, reshuffled clauses.
    # Matched loosely on purpose — no customer reply should ever contain
    # meta-instructions about answering hours/timings questions.
    re.compile(
        r"answer\s+questions?\s+about\s+openings?\s+hours",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"answer\s+(?:timing|time|hours?)\s+questions?\s+from\s+this",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"timings?,?\s+availability\s+windows?\s+or\b",
        flags=re.IGNORECASE,
    ),
    re.compile(r"only\s+start\s+the\s+booking\s+flow", flags=re.IGNORECASE),
    re.compile(r"directly\s+from\s+this\s+block", flags=re.IGNORECASE),
    re.compile(
        r"do\s+not\s+answer\s+from\s+general\s+knowledge", flags=re.IGNORECASE
    ),
)

_INSTRUCTION_ECHO_MARKERS = (
    "authoritative",
    "do not hallucinate",
    "missing field",
    "next field to ask",
    "extracted_entities",
    "extracted_fields",
    "user_intent",
    "field definitions",
    "session context",
    "output format",
    "company knowledge context",
    "business scope",
    "strict - do not",
    "for this business only",
    "response_text",
    "response_message",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# Bullet (U+2022), never an asterisk: WhatsApp bold formatting pairs and
# strips "*" runs, so a "***" mask is eaten (digits join back together) or
# mangles the surrounding bold markers into literal "**". Bullets survive
# every formatter in the outbound pipeline untouched.
_MASK_BULLET = "\u2022"


def mask_phone_for_display(phone: Any) -> str:
    """Mask a phone number for customer-visible messages.

    Shows at most the country prefix + last 2 digits — enough to recognise
    which number a code went to, never enough to reconstruct it. Star-free
    by design (see _MASK_BULLET).
    """
    raw = str(phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if len(digits) <= 6:
        return (digits[:2] + _MASK_BULLET * 2) if len(digits) >= 4 else _MASK_BULLET * 2
    plus = "+" if raw.startswith("+") else ""
    return f"{plus}{digits[:2]}{_MASK_BULLET * 4}{digits[-2:]}"


def mask_email_for_display(email: Any) -> str:
    """Mask an email for customer-visible messages (first letter + domain).

    Star-free for the same pipeline reason as mask_phone_for_display.
    """
    raw = str(email or "").strip()
    if "@" not in raw:
        return _MASK_BULLET * 3
    local, _, domain = raw.partition("@")
    first = (local[:1] or _MASK_BULLET).strip() or _MASK_BULLET
    return f"{first}{_MASK_BULLET * 3}@{domain.strip() or '...'}"


def strip_instruction_echo(text: str) -> str:
    """Remove echoed prompt instructions from a reply, keep the human part.

    Drops any sentence carrying instruction-shaped phrasing. If nothing
    human survives, returns "" so the caller substitutes its safe fallback
    (mirrors the JSON-debris path) instead of sending scaffolding.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""
    lowered_full = raw.lower()
    if not any(rx.search(raw) for rx in _INSTRUCTION_ECHO_RES) and not any(
        marker in lowered_full for marker in _INSTRUCTION_ECHO_MARKERS
    ):
        return raw
    kept = []
    for sentence in _SENTENCE_SPLIT_RE.split(raw):
        chunk = sentence.strip(" \t")
        if not chunk:
            continue
        lowered = chunk.lower()
        if any(rx.search(chunk) for rx in _INSTRUCTION_ECHO_RES):
            continue
        if any(marker in lowered for marker in _INSTRUCTION_ECHO_MARKERS):
            continue
        kept.append(chunk)
    cleaned = " ".join(kept)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    if len(cleaned) < 20:
        return ""
    return cleaned


# Waiver phrasing: the bot agreeing to drop a field ("I can skip that",
# "proceed without it"). Only an offence when a REQUIRED field is still
# pending — waiving an optional field is legal and must pass through.
_WAIVER_MARKERS = (
    "skip that",
    "skip the",
    "skip your",
    "can skip",
    "will skip",
    "could skip",
    "skipping that",
    "without the",
    "without your",
    "proceed without",
    "continue without",
    "go ahead without",
    "no need for",
    "dont need",
    "don't need",
    "do not need",
    "forget it",
    "forget about",
)


def looks_like_field_waiver(
    message: Any,
    missing_fields: Any = None,
    required_names: Any = None,
) -> bool:
    """True when the reply waives a still-required field.

    Production: "I understand. I can skip the phone number too." for a
    REQUIRED phone still in missing_fields — the customer believed the
    field was dropped while the flow still needed it. Optional-field
    waivers pass: only required-and-still-missing counts.
    """
    text = str(message or "").lower().strip()
    if not text:
        return False
    if not any(marker in text for marker in _WAIVER_MARKERS):
        return False
    try:
        missing = [str(m) for m in list(missing_fields or []) if m]
    except Exception:
        return False
    if not missing:
        return False
    try:
        required = {str(r) for r in list(required_names or []) if r}
    except Exception:
        required = set()
    if required:
        pending_required = [m for m in missing if m in required]
        return bool(pending_required)
    # Unknown schema: fail safe — any pending field could be required.
    return True


def sanitize_user_visible_text(raw_message: Any) -> str:
    text = str(raw_message or "")
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_TAG_RE.sub("", text).strip()
    if not text or _REASONING_LEAK_RE.search(text):
        return _FALLBACK_TEXT
    stripped = strip_instruction_echo(text)
    if not stripped:
        return _FALLBACK_TEXT
    # Human-texting style, all channels: em/en dashes read as AI-generated
    # (the prompt forbids them, but the model still slips them, e.g.
    # "Sure — I can help"). Same normalization the WhatsApp transport
    # applies — lifted here so web/admin surfaces get it too.
    stripped = (
        stripped.replace(" — ", ", ").replace("—", ",").replace("–", "-")
    )
    return stripped


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
        # Parsed as machine JSON but contains no human reply (e.g. only an
        # extracted_fields fragment salvaged from a malformed array payload
        # like '[\n "response_text": ...'). Never render the raw structure:
        # production sent such a fragment verbatim to a customer. Fall
        # through to the repair/debris handling below, which ends in the
        # safe fallback when nothing human is recoverable.
        parsed_payload = {}

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
        # Brace text that parsed as neither an object with a reply nor
        # salvageable debris (e.g. json.loads failed above). Still machine
        # structure, never customer prose.
        return sanitize_user_visible_text(""), {}

    if not isinstance(raw_message, str):
        # A dict/list payload with no reply key is machine structure, not
        # prose; str() of it would leak Python repr formatting to chat.
        return sanitize_user_visible_text(""), {}

    return sanitize_user_visible_text(raw_message), parsed_payload