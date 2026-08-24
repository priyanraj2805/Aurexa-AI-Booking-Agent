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
    invalid = invalid_fields or {}
    conflict_map = conflicts or {}
    explicit_unknown = unknown_fields or {}

    unknown_from_extract = {
        key: value for key, value in extracted_fields.items() if key not in allowed_fields
    }
    all_unknown_fields = {**unknown_from_extract, **explicit_unknown}
    if all_unknown_fields:
        diagnostics["unknown_fields"] = all_unknown_fields

    next_optional_skips = set(optional_skips or set())

    for field in reopen_fields or []:
        next_optional_skips.discard(field)
        if field in allowed_fields:
            merged.pop(field, None)

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
    # only re-asked when no fresh value was captured in this same turn.
    reopened_missing = [
        field
        for field in (reopen_fields or [])
        if field in allowed_fields
        and field not in merged
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
