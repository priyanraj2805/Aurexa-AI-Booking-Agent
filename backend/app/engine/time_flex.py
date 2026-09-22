"""Flexible time + confirm parsing for WhatsApp/text booking.

Free-text users type times as `3pm`, `3 pm`, `3:00 pm`, `15:00`, etc.
Slot labels are canonical `format_slot_time` values like `3:00 PM`.
This module parses any reasonable time and matches it to a slot label
by hour/minute — never by string equality.
"""
import difflib
import re
from datetime import time as _time
from typing import List, Optional


def parse_flexible_time(text: str) -> Optional[_time]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    # Reject pure date strings that dateutil would coerce to midnight.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return None
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", raw):
        return None
    # Normalise: "3pm" -> "3 pm", "3.30pm" -> "3:30 pm", "3_00 pm" handled below.
    norm = raw.replace(".", ":").strip()
    norm = re.sub(r"(\d)(am|pm)\b", r"\1 \2", norm)
    norm = re.sub(r"\s+", " ", norm)
    # Require an explicit time token (am/pm or HH:MM) so bare "17" or
    # "17 september" never parses as a time.
    if norm in ("noon", "midday"):
        return _time(12, 0)
    if norm == "midnight":
        return _time(0, 0)
    if not (re.search(r"\b(am|pm)\b", norm) or re.search(r"\b\d{1,2}:\d{2}\b", norm)):
        return None
    try:
        from dateutil.parser import parse as _parse

        return _parse(norm).time().replace(second=0, microsecond=0)
    except Exception:
        return None


def _parse_slot_label(label: str) -> Optional[_time]:
    try:
        from dateutil.parser import parse as _parse

        return _parse(str(label or "").strip()).time().replace(second=0, microsecond=0)
    except Exception:
        return None


def match_time_to_slot_label(text: str, slot_labels: List[str]) -> Optional[str]:
    """Return the canonical slot label matching free-text `text`, or None."""
    want = parse_flexible_time(text)
    if want is None:
        return None
    for label in slot_labels or []:
        got = _parse_slot_label(label)
        if got is not None and got.hour == want.hour and got.minute == want.minute:
            return str(label)
    return None


_CONFIRM_TYPO_TOKENS = {
    "confirm",
    "comfirm",  # screenshot typo
    "confrim",
    "confirmm",
    "cnfirm",
    "confrm",
    "comfirmm",
    "confirmed",
    "confrmed",
    "comfirmed",
}

_SIMPLE_CONFIRMS = {
    "yes", "yep", "yup", "yeah", "sure", "ok", "okay", "k",
    "alright", "allright", "absolutely", "proceed", "book it",
    "go ahead", "please do", "done", "approved", "finalize",
    "finalise", "lock it", "schedule it", "that's it", "thats it",
}


def is_confirm_message(text: str) -> bool:
    msg = (text or "").strip().lower()
    if not msg:
        return False
    # Exact / substring match first (covers "ok" in "okay", "yes please", ...).
    for kw in _SIMPLE_CONFIRMS:
        if kw in msg:
            return True
    for tok in _CONFIRM_TYPO_TOKENS:
        if tok in msg:
            return True
    # Fuzzy token-level match for other 1-2 char typos ("confim", "confrim").
    tokens = re.findall(r"[a-z]+", msg)
    for tok in tokens:
        if len(tok) < 4:
            continue
        if difflib.SequenceMatcher(None, tok, "confirm").ratio() >= 0.8:
            return True
        if difflib.SequenceMatcher(None, tok, "confirmed").ratio() >= 0.8:
            return True
    return False
