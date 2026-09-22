"""Strict step guards for WhatsApp typed entry (QR + Meta-cloud transports).

Product rule: the bot never drifts from the active booking step. While a
step directive is PENDING, typed input is validated DETERMINISTICALLY here —
before the engine/LLM ever sees it. Wrong input gets a correction naming the
exact expected format; the bot never guesses.

Key fix: bare numbers ("2") are NEVER treated as positional picks for
SELECT_DATE. Simple-text prompts show no numbers, so index-picking silently
mapped a lone digit to the Nth hidden option. Dates must be typed
in full. For SELECT_SLOT the positional shortcut is kept (times are commonly
picked by number) — see match_slot_index.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BARE_NUMBER_RE = re.compile(r"^\d{1,2}$")

_CHANGE_WORDS = frozenset(
    {"change", "edit", "modify", "correction", "mistake", "wrong", "update"}
)

# Objection / concern handling mid-booking. Patterns detect the CATEGORY;
# every word of the reply comes from live slots (business name, field
# labels/descriptions, pending step) + the admin-chosen tone. No static copy.
_WHY_PATTERNS = (
    "why do you need",
    "why do i need",
    "why need",
    "why ask",
    "why should i",
    "why is this",
    "why is it",
    "need to give",
    "have to give",
    "what for",
    "what do you need",
    "reason for",
    "is it necessary",
    "is this necessary",
    "necessary",
    "mandatory",
    "compulsory",
    "why is it required",
    "why required",
)

_REFUSE_PATTERNS = (
    "don't want to give",
    "dont want to give",
    "do not want to give",
    "don't want to share",
    "dont want to share",
    "don't want to tell",
    "wont give",
    "won't give",
    "will not share",
    "will not give",
    "rather not",
    "skip this",
    "skip that",
    "skip it",
)

_NO_BOOKING_PATTERNS = (
    "don't want to book",
    "dont want to book",
    "do not want to book",
    "not interested",
    "no need to book",
    "don't need appointment",
    "changed my mind",
)

_PRIVACY_PATTERNS = (
    "privacy",
    "misuse",
    "misused",
    "spam",
    "sell my data",
    "share my data",
    "data safe",
    "is it safe",
    "is this safe",
    "is it secure",
    "is this secure",
    "secure",
    "hack",
)

_FIELD_WORDS = {
    "name": ("name",),
    "email": ("email", "mail"),
    "phone": ("phone", "number", "mobile", "contact"),
    "date": ("date", "day"),
    "time": ("time", "slot", "hour"),
}


def _field_mentioned(lowered: str) -> Optional[str]:
    for field, words in _FIELD_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
            return field
    return None


def detect_objection(
    text: str,
    collected: Optional[Dict[str, Any]] = None,
    step_field: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Detect booking objections/concerns that deserve acknowledgment.

    Returns {"category", "field"} or None. Categories: why_needed, refuse,
    no_booking, privacy. `field` is the concerned field (mentioned, else the
    current step's field, else None). Never claims general knowledge
    questions — those keep flowing to RAG.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return None
    collected = collected or {}

    def _has(patterns) -> bool:
        return any(p in lowered for p in patterns)

    if _has(_NO_BOOKING_PATTERNS):
        return {"category": "no_booking", "field": None}
    if _has(_PRIVACY_PATTERNS):
        field = _field_mentioned(lowered) or step_field
        if field is not None and field not in collected and field != step_field:
            field = step_field
        return {"category": "privacy", "field": field}
    if _has(_REFUSE_PATTERNS):
        field = _field_mentioned(lowered) or step_field
        return {"category": "refuse", "field": field}
    if _has(_WHY_PATTERNS):
        field = _field_mentioned(lowered) or step_field
        return {"category": "why_needed", "field": field}
    return None


def build_objection_brief(
    *,
    category: str,
    field: Optional[str],
    business_name: str,
    tone: Optional[str],
    field_label: str = "",
    field_description: str = "",
    field_required: bool = True,
    required_labels: Optional[List[str]] = None,
    step_title: str = "",
    step_prompt: str = "",
) -> Dict[str, Any]:
    """Assemble the ONLY facts the objection reply may use.

    Every value is caller-supplied live data (org config, field
    definitions, pending directive). The presenter may word, never invent.
    """
    return {
        "category": category,
        "field": field,
        "business_name": business_name or "",
        "tone": tone or "friendly",
        "field_label": field_label or (field or ""),
        "field_description": field_description or "",
        "field_required": bool(field_required),
        "required_labels": list(required_labels or []),
        "step_title": step_title or "",
        "step_prompt": step_prompt or "",
    }

# Field-scoped change phrases (mirror the engine's deterministic overrides).
_FIELD_CHANGE_KEYWORDS = {
    "date": [
        "change the date", "change date", "different date", "another date",
        "wrong date", "pick another date", "choose another date",
        "change my date", "switch date", "update date", "new date",
        "reschedule",
    ],
    "time": [
        "change the time", "change time", "change my time", "wrong time",
        "update time", "different time", "another time", "new time",
    ],
    "name": ["change name", "change my name", "update name", "wrong name"],
    "email": [
        "change email", "change my email", "update email", "wrong email",
        "different email",
    ],
    "phone": [
        "change phone", "change my phone", "update phone", "wrong phone",
        "change number", "change my number", "different number",
    ],
}

_STEP_FIELD = {
    "SELECT_DATE": "date",
    "SELECT_SLOT": "time",
    "SELECT_TIME": "time",
}

_CANCEL_BYPASS_RE = re.compile(
    r"\b(cancel|stop|cancel booking|never mind|forget it)\b"
)


def is_cancel_bypass(text: str) -> bool:
    """True when the user wants out — never trap cancel behind a step guard.

    Cancellation has its own engine flow; the directive layer must fall
    through untouched so it can run.
    """
    return bool(_CANCEL_BYPASS_RE.search((text or "").lower()))


def field_slot(
    field_definitions: Any, field_name: Optional[str]
) -> Tuple[str, str, bool]:
    """Live (label, description, required) for a field from admin definitions.

    Accepts dicts (JSONB) or objects. Falls back to a title-cased name with
    no description — never invented business copy.
    """
    label = (field_name or "").replace("_", " ").strip().title() or "details"
    description = ""
    required = True
    try:
        defs = field_definitions or []
        for fd in defs:
            if isinstance(fd, dict):
                name, lab, desc, req = (
                    fd.get("name"), fd.get("label"), fd.get("description"), fd.get("required", True),
                )
            else:
                name, lab = getattr(fd, "name", None), getattr(fd, "label", None)
                desc, req = getattr(fd, "description", ""), getattr(fd, "required", True)
            if name == field_name:
                if lab:
                    label = str(lab)
                if desc:
                    description = str(desc)
                required = bool(req)
                break
    except Exception:
        pass
    return label, description, required


def required_labels(field_definitions: Any) -> List[str]:
    """Live labels of required fields (for 'what we need' lists)."""
    labels: List[str] = []
    try:
        for fd in field_definitions or []:
            if isinstance(fd, dict):
                name, lab, req = fd.get("name"), fd.get("label"), fd.get("required", True)
            else:
                name, lab, req = getattr(fd, "name", None), getattr(fd, "label", None), getattr(fd, "required", True)
            if req and name:
                labels.append(str(lab or name))
    except Exception:
        pass
    return labels


def detect_field_change_request(
    text: str, collected: Optional[Dict[str, Any]], step_field: Optional[str] = None
) -> Optional[str]:
    """Detect 'change my <field>' for an ALREADY-collected field.

    Returns the field to clear, or None. The current step's own field is
    excluded (its input is validated normally), as are uncollected fields.
    """
    lowered = (text or "").lower()
    collected = collected or {}
    for field, keywords in _FIELD_CHANGE_KEYWORDS.items():
        if field == step_field:
            continue
        if field not in collected:
            continue
        if any(kw in lowered for kw in keywords):
            return field
    return None


def apply_collected_field_change(
    collected: Optional[Dict[str, Any]], field: str
) -> Dict[str, Any]:
    """Clear a field plus its dependents (mirrors the engine overrides).

    Changing the date invalidates the time/slot picked for the old date;
    changing the time drops the slot lock. Personal data is never touched.
    """
    cd = dict(collected or {})
    cd.pop(field, None)
    if field == "date":
        cd.pop("time", None)
        cd.pop("selected_slot_id", None)
        cd.pop("locked_slot_id", None)
        cd.pop("locked_slot_start_utc", None)
    if field == "time":
        cd.pop("selected_slot_id", None)
        cd.pop("locked_slot_id", None)
        cd.pop("locked_slot_start_utc", None)
    return cd


def _format_ddmmyyyy(iso_date: str) -> Optional[str]:
    """ISO date -> DD/MM/YYYY display form. Pure formatting, no literals."""
    try:
        from datetime import date as _date

        parts = _date.fromisoformat(iso_date).timetuple()
        return f"{parts.tm_mday:02d}/{parts.tm_mon:02d}/{parts.tm_year}"
    except (ValueError, TypeError):
        return None


def example_date_hints(
    option_values: List[str], *, tz: str = "UTC", max_days: int = 30
) -> List[str]:
    """Build live typed-format examples from real option ISOs.

    Prefers actual directive option values (ISO -> DD/MM/YYYY display);
    falls back to near-future horizon dates in the org timezone. Never
    returns hardcoded dates — every hint is computed from live data.
    """
    hints: List[str] = []
    for iso in option_values or []:
        formatted = _format_ddmmyyyy(str(iso))
        if formatted and formatted not in hints:
            hints.append(formatted)
        if len(hints) >= 2:
            break
    if len(hints) < 2:
        try:
            from datetime import datetime as _dt

            try:
                from zoneinfo import ZoneInfo as _ZI
            except ImportError:
                from backports.zoneinfo import ZoneInfo as _ZI  # type: ignore[no-redef]

            try:
                _tz = _ZI(tz)
            except Exception:
                _tz = _ZI("UTC")
            from datetime import timedelta as _td

            _today = _dt.now(_tz).date()
            for _ahead in (1, 2):
                _candidate = (_today + _td(days=_ahead)).isoformat()
                _formatted = _format_ddmmyyyy(_candidate)
                if _formatted and _formatted not in hints:
                    hints.append(_formatted)
                if len(hints) >= 2:
                    break
        except Exception:
            pass
    return hints[:2]


def example_time_hints(slot_labels: List[str]) -> List[str]:
    """Live time examples are just the first real available slot labels."""
    return [str(lbl) for lbl in (slot_labels or []) if str(lbl).strip()][:2]


def is_bare_number(text: str) -> bool:
    """True for a lone 1-2 digit string like "2" — never a date or time."""
    return bool(BARE_NUMBER_RE.match((text or "").strip()))


def match_option_by_text(options: List[Dict[str, Any]], text: str) -> Optional[Dict[str, Any]]:
    """Exact label/value/option-id match (case-insensitive). No index logic."""
    norm = (text or "").strip().lower()
    if not norm:
        return None
    for opt in options or []:
        label = str(opt.get("label") or "").strip().lower()
        value = str(opt.get("value") or "").strip().lower()
        oid = str(opt.get("id") or "").strip().lower()
        if norm in (label, value, oid) or (label and norm == label.replace(" ", "")):
            return opt
    return None


def match_slot_index(options: List[Dict[str, Any]], text: str) -> Optional[Dict[str, Any]]:
    """Legacy positional pick for time slots only ("2" -> 2nd slot)."""
    try:
        idx = int((text or "").strip()) - 1
    except (ValueError, TypeError):
        return None
    opts = options or []
    if 0 <= idx < len(opts):
        return opts[idx]
    return None


def match_confirm_intent(text: str) -> Optional[str]:
    """Returns "confirm" | "change" | None (typo-tolerant confirm)."""
    try:
        from app.engine.time_flex import is_confirm_message as _is_confirm

        if _is_confirm(text):
            return "confirm"
    except Exception:
        lowered = (text or "").strip().lower()
        if lowered in {"confirm", "yes", "ok", "done"}:
            return "confirm"
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    if words & _CHANGE_WORDS:
        return "change"
    return None


def _format_hint(hints: List[str]) -> str:
    """'*a* or *b*' from live hints; format words when none available."""
    live = [f"*{h}*" for h in hints if h]
    if live:
        return " or ".join(live)
    return "the day, month and year in full"


def validate_typed_date(
    text: str,
    *,
    tz: str = "UTC",
    max_days: int = 30,
    examples: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[str], str]:
    """Strict date check for typed entry.

    Returns (iso_date | None, correction | None, reason). Reasons:
    "bare_number" | "invalid" | "past" | "too_far" | "ok".
    Every correction cites live `examples` (real option/horizon dates) —
    no hardcoded sample dates anywhere.
    """
    from app.engine.validators import DataValidator

    raw = (text or "").strip()
    hint = _format_hint(list(examples or []))
    if is_bare_number(raw):
        return (
            None,
            f'"{raw}" is not a date. \u274c\n'
            f"Please type the full meeting date like {hint}.",
            "bare_number",
        )
    iso, err = DataValidator._validate_date(raw, tz, max_days)
    if iso:
        return iso, None, "ok"
    err_low = (err or "").lower()
    if "past" in err_low:
        return (
            None,
            "That date has already passed. \u274c\n"
            f"Please type a future meeting date like {hint}.",
            "past",
        )
    if "more than" in err_low or "future" in err_low:
        return (
            None,
            "We take bookings only within the available window. \u274c\n"
            f"Please type a meeting date like {hint}.",
            "too_far",
        )
    return (
        None,
        "That doesn't look like a valid date. \u274c\n"
        f"Please type the meeting date like {hint}.",
        "invalid",
    )


def date_correction(raw: str, correction: str, horizon_text: str = "") -> str:
    """Full correction body for a rejected date (never a numbered list)."""
    body = correction
    if horizon_text:
        body += f"\n{horizon_text}"
    return body


def time_correction(
    raw: str, hours_text: str = "", examples: Optional[List[str]] = None
) -> str:
    """Full correction body for a rejected time (live examples, never numbers)."""
    live = [f"*{h}*" for h in (examples or []) if h]
    like = f" like {' or '.join(live)}" if live else " exactly as shown"
    body = (
        f'"{(raw or "").strip()}" is not an available time. \u274c\n'
        f"Please type a time from the available hours{like}."
    )
    if hours_text:
        body += f"\n{hours_text}"
    return body


def confirm_correction() -> str:
    """Full correction body for a non-confirm, non-change reply."""
    return (
        "Please reply *CONFIRM* to book your appointment "
        "or *CHANGE* to edit the details."
    )
