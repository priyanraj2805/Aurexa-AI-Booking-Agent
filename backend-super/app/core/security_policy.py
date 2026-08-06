"""Shared security policy helpers (Phase 8).

- get_security_settings: single-row super_admin.security_settings reader.
- check_ip_allowed: IP allowlist enforcement, used by get_current_admin.
- validate_password_policy: password policy rules used by /auth/change-password.
"""
import ipaddress
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_SETTINGS: dict[str, Any] = {
    "mfa_enforced": True,
    "password_policy": {
        "min_length": 8,
        "require_upper": True,
        "require_digit": True,
        "require_special": True,
    },
    "ip_allowlist": {"enabled": False, "cidrs": []},
}


async def get_security_settings(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT mfa_enforced, password_min_length,
                       password_require_upper, password_require_digit,
                       password_require_special,
                       ip_allowlist_enabled, ip_allowlist_cidrs
                FROM super_admin.security_settings WHERE id = 1
                """
            )
        )
    ).mappings().first()
    if not row:
        return dict(DEFAULT_SETTINGS)
    return {
        "mfa_enforced": row["mfa_enforced"],
        "password_policy": {
            "min_length": row["password_min_length"],
            "require_upper": row["password_require_upper"],
            "require_digit": row["password_require_digit"],
            "require_special": row["password_require_special"],
        },
        "ip_allowlist": {
            "enabled": row["ip_allowlist_enabled"],
            "cidrs": row["ip_allowlist_cidrs"] or [],
        },
    }


def check_ip_allowed(client_ip: str, settings_row: dict[str, Any]) -> bool:
    """True when the allowlist is off or the IP matches a configured CIDR."""
    allowlist = settings_row.get("ip_allowlist") or {}
    if not allowlist.get("enabled"):
        return True
    try:
        addr = ipaddress.ip_address(client_ip.split(",")[0].strip())
    except ValueError:
        return False
    for cidr in allowlist.get("cidrs") or []:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def validate_password_policy(password: str, policy: dict[str, Any]) -> None:
    """422 unless the password satisfies the configured policy."""
    errors = []
    if len(password) < policy.get("min_length", 8):
        errors.append(f"at least {policy.get('min_length', 8)} characters")
    if policy.get("require_upper") and not re.search(r"[A-Z]", password):
        errors.append("an uppercase letter")
    if policy.get("require_digit") and not re.search(r"\d", password):
        errors.append("a digit")
    if policy.get("require_special") and not re.search(r"[^A-Za-z0-9]", password):
        errors.append("a special character")
    if errors:
        raise HTTPException(
            status_code=422,
            detail="Password must contain " + ", ".join(errors),
        )
