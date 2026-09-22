"""Tenant RBAC — principal + permission guards (mirrors backend-super).

Permissions are loaded from the DB on EVERY request (never trusted from the
token), so grant changes take effect immediately. Roles are lazily seeded
per org on first use: orgs without roles get the seed set and every active
user gets the default role — existing behavior is fully preserved.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.v1.endpoints.auth import get_current_user
from app.core.rbac_seed import TENANT_PERMISSIONS, TENANT_ROLES
from app.db.database import get_db
from app.models.organization import Organization
from app.models.rbac import DenyRule, Permission, Role, RolePermission, UserRole
from app.models.user import User

logger = logging.getLogger(__name__)


class CurrentPrincipal:
    """Authenticated tenant user with org + role + permissions attached."""

    def __init__(
        self,
        user: User,
        org: Organization,
        role_codes: List[str],
        permissions: List[str],
    ):
        self.user = user
        self.org = org
        self.org_id = user.org_id
        self.role_codes = role_codes
        self.permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


async def ensure_org_roles(db: AsyncSession, org: Organization) -> None:
    """Idempotent per-org seed: roles + links, default role to role-less users."""
    existing = (
        await db.execute(select(Role).where(Role.org_id == org.id))
    ).scalars().all()
    if not existing:
        perm_rows = (
            await db.execute(
                select(Permission).where(Permission.code.in_(TENANT_PERMISSIONS))
            )
        ).scalars().all()
        perm_by_code = {p.code: p for p in perm_rows}
        default_role = None
        for role_def in TENANT_ROLES:
            role = Role(
                id=uuid.uuid4(),
                org_id=org.id,
                code=role_def["code"],
                name=role_def["name"],
                is_system=True,
                inherit_to_children=bool(role_def.get("inherit_to_children", False)),
            )
            db.add(role)
            await db.flush()
            for code in role_def["permissions"]:
                perm = perm_by_code.get(code)
                if perm is not None:
                    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
            if role_def.get("default"):
                default_role = role
        await db.commit()
        existing = (
            await db.execute(select(Role).where(Role.org_id == org.id))
        ).scalars().all()
        if default_role is None:
            default_role = next(
                (r for r in existing if r.code == TENANT_ROLES[0]["code"]), None
            )
        if default_role is not None:
            users = (
                await db.execute(
                    select(User).where(
                        User.org_id == org.id, User.is_active.is_(True)
                    )
                )
            ).scalars().all()
            for u in users:
                has = (
                    await db.execute(
                        select(UserRole).where(
                            UserRole.user_id == u.id, UserRole.org_id == org.id
                        )
                    )
                ).scalars().first()
                if has is None:
                    db.add(
                        UserRole(
                            user_id=u.id, role_id=default_role.id, org_id=org.id
                        )
                    )
            await db.commit()


async def get_current_principal(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CurrentPrincipal:
    """Resolve user → org → roles → permissions (DB is the authority)."""
    org = (
        await db.execute(
            select(Organization).where(Organization.id == user.org_id)
        )
    ).scalars().first()
    if org is None or not org.is_active:
        raise HTTPException(status_code=403, detail="Organization is disabled")
    await ensure_org_roles(db, org)

    # GCP evaluation order: DENY first (own + inherited), then ALLOW as the
    # union of own bindings + inherited folder bindings. Expired bindings
    # (request.time >= expires_at) grant nothing.
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(Role.code, Permission.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .join(
                RolePermission, RolePermission.role_id == Role.id
            )
            .join(
                Permission, Permission.id == RolePermission.permission_id
            )
            .where(
                UserRole.user_id == user.id,
                UserRole.org_id == org.id,
                (UserRole.expires_at.is_(None)) | (UserRole.expires_at > now),
            )
        )
    ).all()
    role_codes = sorted({r[0] for r in rows})
    allowed = {r[1] for r in rows}

    denied = {
        r[0]
        for r in (
            await db.execute(
                select(DenyRule.permission_code).where(
                    DenyRule.org_id.in_([org.id]),
                    DenyRule.is_active.is_(True),
                )
            )
        ).all()
    }
    permissions = sorted(allowed - denied)
    return CurrentPrincipal(
        user=user, org=org, role_codes=role_codes, permissions=permissions
    )


def require_permission(permission: str):
    """Dependency guard: 403 unless the principal holds the permission."""

    async def dependency(
        principal: CurrentPrincipal = Depends(get_current_principal),
    ) -> CurrentPrincipal:
        if not principal.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Missing permission: {permission}",
            )
        return principal

    return dependency


def require_any_permission(*permissions: str):
    """Dependency guard: 403 unless the principal holds ANY permission."""

    async def dependency(
        principal: CurrentPrincipal = Depends(get_current_principal),
    ) -> CurrentPrincipal:
        if not any(principal.has_permission(p) for p in permissions):
            raise HTTPException(
                status_code=403,
                detail=f"Missing one of permissions: {', '.join(permissions)}",
            )
        return principal

    return dependency
