from __future__ import annotations

from datetime import date
from typing import Any, Dict, List


def _error(tool: str, issues: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "INVALID_TOOL_ARGS",
            "tool": tool,
            "issues": issues,
        },
    }


def validate_field_save_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    field_name = str(payload.get("field_name", "")).strip().lower()
    value = str(payload.get("value", "")).strip()

    issues: List[Dict[str, str]] = []
    if not field_name:
        issues.append({"field": "field_name", "reason": "required"})
    if not value:
        issues.append({"field": "value", "reason": "required"})

    if issues:
        return _error("save_field_value", issues)

    return {
        "ok": True,
        "normalized": {
            "field_name": field_name,
            "value": value,
        },
    }


def validate_booking_create_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    target_date = str(payload.get("target_date", "")).strip()
    time = str(payload.get("time", "")).strip()

    issues: List[Dict[str, str]] = []
    if not target_date:
        issues.append({"field": "target_date", "reason": "required"})
    else:
        try:
            date.fromisoformat(target_date)
        except ValueError:
            issues.append({"field": "target_date", "reason": "invalid_date"})

    if not time:
        issues.append({"field": "time", "reason": "required"})

    if issues:
        return _error("create_booking_record", issues)

    return {
        "ok": True,
        "normalized": {
            "target_date": target_date,
            "time": time,
        },
    }
