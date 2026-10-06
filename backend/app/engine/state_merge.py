from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set


def _ordered_unique(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


# A person's name is a short label, never a sentence. These bounds are
# deliberately loose at the top end (long names are real) and only reject
# values that are unambiguously not names. Four words covers "Mary Jane
# Watson-Kelly" and "Dr Ananya Sharma" with room to spare; a real question to
# the business ("I want to know about dotstark") is six words and is rejected.
_NAME_MAX_CHARS = 80
_NAME_MAX_WORDS = 4

_IMPLAUSIBLE_NAME_REASON = (
    "That looks like a question or a sentence rather than a name. "
    "Could you tell me just your full name?"
)


def _looks_like_sentence(value: Any) -> bool:
    """True when a free-text field value is really a sentence/question."""
    text = str(value or "").strip()
    if not text:
        return True
    if text.endswith("?"):
        return True
    if len(text) > _NAME_MAX_CHARS:
        return True
    return len([w for w in text.split() if w]) > _NAME_MAX_WORDS


def reject_implausible_values(
    extracted_fields: Dict[str, Any],
    allowed_fields: Set[str],
) -> Dict[str, str]:
    """Move obviously non-values out of the extraction and into `invalid_fields`.

    A model handed "I want to know about dotstark" while the active step was
    "full name" returned that whole sentence as the customer's name. The merge
    then treated the name as collected, dropped it from the missing queue, and
    the customer was never asked again — a booking could be written with
    `customer_name = "I want to know about dotstark"`. Asking the customer a
    question is not a field value, so it is rejected here rather than merged.

    Only free-text identity fields are checked. Dates, times, emails and phone
    numbers have their own validators, and rejecting them here would duplicate
    (and fight) that logic.
    """
    rejected: Dict[str, str] = {}
    for key, value in (extracted_fields or {}).items():
        if key not in allowed_fields:
            continue
        if str(key).strip().lower() != "name":
            continue
        if _looks_like_sentence(value):
            rejected[key] = _IMPLAUSIBLE_NAME_REASON
    return rejected


def apply_state_merge(
    *,
    current_fields: Dict[str, Any],
    extracted_fields: Dict[str, Any],
    required_fields: List[str],
    optional_fields: Optional[List[str]] = None,
    optional_skips: Optional[Set[str]] = None,
    invalid_fields: Optional[Dict[str, str]] = None,
    skipped_fields: Optional[List[str]] = None,
    conflicts: Optional[Dict[str, List[Any]]] = None,
    unknown_fields: Optional[Dict[str, Any]] = None,
    reopen_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    optional_field_set = set(optional_fields or [])
    allowed_fields = set(required_fields) | optional_field_set
    diagnostics: Dict[str, Any] = {}

    merged = dict(current_fields)
    deltas: Dict[str, Any] = {}
    conflict_map = conflicts or {}
    explicit_unknown = unknown_fields or {}

    # Safety net: a question is not a field value. Runs before the unknown-field
    # and invalid-field passes so a rejected capture is reported the same way as
    # any other invalid value (kept out of `merged`, re-asked in follow-up).
    implied_invalid = reject_implausible_values(extracted_fields, allowed_fields)
    invalid: Dict[str, str] = dict(implied_invalid)
    invalid.update(invalid_fields or {})

    unknown_from_extract = {
        key: value for key, value in extracted_fields.items() if key not in allowed_fields
    }
    all_unknown_fields = {**unknown_from_extract, **explicit_unknown}
    if all_unknown_fields:
        diagnostics["unknown_fields"] = all_unknown_fields

    next_optional_skips = set(optional_skips or set())

    for field in reopen_fields or []:
        next_optional_skips.discard(field)
    # NOTE: reopen never deletes the stored value. An earlier version popped
    # it, but persistence merges (never prunes) — the next turn resurrected
    # the "deleted" value from the row and the correction was silently lost
    # ("change my number" acknowledged, old number kept). The field is
    # re-asked via reopened_missing below; the stored value stands until a
    # fresh one arrives.

    for field in skipped_fields or []:
        if field in optional_field_set:
            next_optional_skips.add(field)

    if invalid:
        diagnostics["invalid_fields"] = invalid

    if conflict_map:
        diagnostics["conflicts"] = conflict_map

    for key, value in extracted_fields.items():
        if key in all_unknown_fields:
            continue
        if key in invalid:
            continue
        if key in conflict_map:
            continue
        # Never clobber a saved value with null/empty: an LLM "null" for an
        # already-collected field is an omission, not a deletion. There is no
        # clear-field UX, so empty can never be fresher than stored data.
        if (value is None or value == "") and merged.get(key) not in (
            None,
            "",
        ):
            continue
        if merged.get(key) != value:
            merged[key] = value
            deltas[key] = value

    required_missing = [name for name in required_fields if name not in merged]
    invalid_follow_up = [name for name in invalid if name in allowed_fields]
    conflict_follow_up = [name for name in conflict_map if name in allowed_fields]
    follow_up = _ordered_unique(required_missing + invalid_follow_up + conflict_follow_up)
    follow_up = [field for field in follow_up if field not in next_optional_skips]

    # Reopened fields must lead the follow-up queue so the widget asks for
    # the field being changed (not the next missing one) first. A field is
    # only re-asked when no fresh value was captured in this same turn
    # (deltas carries exactly this turn's accepted values — the stored value
    # no longer distinguishes fresh from stale since reopen keeps it).
    reopened_missing = [
        field
        for field in (reopen_fields or [])
        if field in allowed_fields
        and field not in deltas
        and field not in next_optional_skips
    ]
    if reopened_missing:
        follow_up = _ordered_unique(reopened_missing + follow_up)

    return {
        "collected_fields": merged,
        "missing_fields": required_missing,
        "optional_skips": next_optional_skips,
        "pending_conflict": conflict_map,
        "captured_deltas": deltas,
        "follow_up_fields": follow_up,
        "diagnostics": diagnostics,
    }
