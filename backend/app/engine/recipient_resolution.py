from __future__ import annotations

from typing import Any, Iterable


def resolve_email_from_collected_data(
    collected_data: Any,
    field_definitions: Iterable[Any] | None = None,
) -> str:
    if not isinstance(collected_data, dict):
        return ""

    candidate_keys: list[str] = []

    if field_definitions:
        for field in field_definitions:
            field_name = None
            field_type = None
            if isinstance(field, dict):
                field_name = field.get("name")
                field_type = field.get("type")
            else:
                field_name = getattr(field, "name", None)
                field_type = getattr(field, "type", None)
            if (
                isinstance(field_name, str)
                and field_name.strip()
                and str(field_type or "").strip().lower() == "email"
            ):
                candidate_keys.append(field_name.strip())

    candidate_keys.append("email")
    for key in collected_data.keys():
        if isinstance(key, str) and "email" in key.lower():
            candidate_keys.append(key)

    seen: set[str] = set()
    for key in candidate_keys:
        if key in seen:
            continue
        seen.add(key)
        value = collected_data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    return ""
