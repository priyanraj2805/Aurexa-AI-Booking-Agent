"""
RBAC — current admin dependency and the require_permission guard.

Roles and permissions are loaded from the DB on EVERY request (never trusted
from the token), so role changes take effect immediately.
"""
from typing import List, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_token, PURPOSE_ACCESS
from app.core.security_policy import check_ip_allowed, get_security_settings
from app.db.database import get_db
from app.models.super_admin import SuperAdmin, Role, Permission, role_permissions


class CurrentAdmin:
    """Authenticated super admin with role + permissions attached."""

    def __init__(
        self,
        admin: SuperAdmin,
        role_code: str,
        role_name: str,
        permissions: List[str],
    ):
        self.id = admin.id
        self.email = admin.email
        self.full_name = admin.full_name
        self.role_id = admin.role_id
        self.role_code = role_code
        self.role_name = role_name
        self.permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


async def get_current_admin(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
    super_admin_token: Optional[str] = Cookie(None),
    request: Request = None,
) -> CurrentAdmin:
    """Resolve the current admin from Bearer header or the super_admin_token
    HttpOnly cookie. Loads role + permissions fresh from the DB."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif super_admin_token:
        token = super_admin_token

    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    payload = decode_token(token, expected_purpose=PURPOSE_ACCESS)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    # Phase 8: IP allowlist enforcement (settings row, enabled → CIDR match).
    settings_row = await get_security_settings(db)
    client_ip = request.client.host if request else "unknown"
    xff = request.headers.get("x-forwarded-for") if request else None
    if xff:
        client_ip = xff.split(",")[0].strip()
    if not check_ip_allowed(client_ip, settings_row):
        raise HTTPException(
            status_code=403,
            detail="Access denied: IP not in allowlist",
        )

    try:
        admin_id = payload["sub"]
        result = await db.execute(
            select(SuperAdmin).where(SuperAdmin.id == admin_id)
        )
        admin = result.scalar_one_or_none()
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    if admin.status != "active":
        raise HTTPException(status_code=403, detail="Admin account is disabled")

    # Load role + permissions fresh from the DB
    role_result = await db.execute(select(Role).where(Role.id == admin.role_id))
    role = role_result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=403, detail="Admin role not found")

    perms_result = await db.execute(
        select(Permission.code)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .where(role_permissions.c.role_id == role.id)
    )
    permissions = [row[0] for row in perms_result.all()]

    return CurrentAdmin(
        admin=admin,
        role_code=role.code,
        role_name=role.name,
        permissions=permissions,
    )


def require_permission(permission: str):
    """Dependency guard: 403 unless the current admin has the permission."""

    def dependency(current: CurrentAdmin = Depends(get_current_admin)) -> CurrentAdmin:
        if not current.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Missing permission: {permission}",
            )
        return current

    return dependency


def require_any_permission(*permissions: str):
    """Dependency guard: 403 unless the current admin has ANY of the
    permissions (used where one route serves multiple request types, e.g.
    the approval queue serving org.delete AND billing.refund)."""

    def dependency(current: CurrentAdmin = Depends(get_current_admin)) -> CurrentAdmin:
        if not any(current.has_permission(p) for p in permissions):
            raise HTTPException(
                status_code=403,
                detail=f"Missing one of permissions: {', '.join(permissions)}",
            )
        return current

    return dependency
