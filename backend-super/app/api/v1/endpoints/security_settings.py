"""Security policy settings (Phase 8) — MFA policy, password policy, IP allowlist.

Stored in the single-row super_admin.security_settings table. Enforcement:
  - IP allowlist    → app.core.rbac.get_current_admin (every request)
  - password policy → /auth/change-password
"""
import ipaddress
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.rbac import CurrentAdmin, require_permission
from app.core.security_policy import get_security_settings
from app.db.database import get_db

router = APIRouter()


class PasswordPolicy(BaseModel):
    min_length: int = Field(8, ge=6, le=64)
    require_upper: bool = True
    require_digit: bool = True
    require_special: bool = True


class IPAllowlist(BaseModel):
    enabled: bool = False
    cidrs: list[str] = []


class SecuritySettingsUpdate(BaseModel):
    mfa_enforced: bool = True
    password_policy: PasswordPolicy
    ip_allowlist: IPAllowlist


def _validate_cidrs(cidrs: list[str]) -> None:
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid CIDR: {cidr}")


@router.get("/settings")
async def read_security_settings(
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("security.manage")),
):
    return await get_security_settings(db)


@router.put("/settings")
async def update_security_settings(
    payload: SecuritySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("security.manage")),
):
    _validate_cidrs(payload.ip_allowlist.cidrs)
    old = await get_security_settings(db)
    await db.execute(
        text(
            """
            UPDATE super_admin.security_settings
            SET mfa_enforced = :mfa,
                password_min_length = :min_len,
                password_require_upper = :upper,
                password_require_digit = :digit,
                password_require_special = :special,
                ip_allowlist_enabled = :ip_on,
                ip_allowlist_cidrs = :cidrs,
                updated_at = now(),
                updated_by = :by
            WHERE id = 1
            """
        ),
        {
            "mfa": payload.mfa_enforced,
            "min_len": payload.password_policy.min_length,
            "upper": payload.password_policy.require_upper,
            "digit": payload.password_policy.require_digit,
            "special": payload.password_policy.require_special,
            "ip_on": payload.ip_allowlist.enabled,
            "cidrs": json.dumps(payload.ip_allowlist.cidrs),
            "by": admin.id,
        },
    )
    await write_audit_log(
        actor_email=admin.email,
        action="security.settings.update",
        resource_type="security_settings",
        resource_id="1",
        old_values=old,
        new_values=payload.model_dump(),
    )
    await db.commit()
    return await get_security_settings(db)
