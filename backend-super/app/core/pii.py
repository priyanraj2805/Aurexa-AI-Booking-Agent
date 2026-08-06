"""PII masking for support tooling (Phase 8).

Applied BEFORE any transcript reaches an admin screen. Emails, phone numbers
and common PII keys (name/email/phone) inside session state are masked.
Masking is one-way and irreversible — there is no unmask path.
"""
import re
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d[\d\s\-().]{6,}\d)(?!\d)"
)
_PII_KEYS = frozenset(
    {
        "name",
        "full_name",
        "customer_name",
        "email",
        "customer_email",
        "phone",
        "phone_number",
        "mobile",
        "whatsapp",
        "channel_user_id",
    }
)


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    visible = local[:2] if len(local) > 2 else local[:1] or "*"
    return f"{visible}{'*' * 3}@***"


def mask_text(text: str) -> str:
    """Mask emails and phone numbers inside free text."""
    masked = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
    masked = _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), masked)
    return masked


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "****"
    return f"+{'*' * (len(digits) - 4)}{digits[-4:]}"


def mask_value(key: str, value: Any) -> Any:
    """Mask a single value based on its key."""
    if value is None:
        return None
    if key.lower() in _PII_KEYS:
        s = str(value)
        if "@" in s:
            return mask_email(s)
        if any(ch.isdigit() for ch in s):
            return mask_phone(s)
        if len(s) >= 2:
            return f"{s[0]}{'*' * min(4, len(s) - 1)}"
        return "*" * len(s)
    return value


def mask_dict(data: dict, _path: str = "") -> dict:
    """Recursively mask PII keys in a nested dict (e.g. session state_data)."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _PII_KEYS:
            out[key] = mask_value(key, value)
        elif isinstance(value, dict):
            out[key] = mask_dict(value, _path + "." + key)
        elif isinstance(value, list):
            out[key] = [
                mask_dict(item, _path + "." + key)
                if isinstance(item, dict)
                else mask_value(key, item)
                for item in value
            ]
        else:
            out[key] = value
    return out
