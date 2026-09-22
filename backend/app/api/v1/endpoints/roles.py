"""GCP-style IAM policy management for the tenant plane.

Custom roles (user-defined bundles), bindings (user ↔ role, optionally
time-bound), and deny rules. System (seed) roles are immutable like GCP
predefined roles. Anti-escalation: a caller can never grant a permission
they do not hold, and cannot strand the org without roles.assign holders.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.rbac import CurrentPrincipal, get_current_principal, require_permission
from app.db.database import get_db
from app.models.organization import Organization
from app.models.rbac import DenyRule, Permission, Role, RolePermission, UserRole
from app.models.user import User

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────────────

class RoleOut(BaseModel):
    id: str
    code: str
    name: str
    is_system: bool
    inherit_to_children: bool
    version: int
    permissions: List[str] = []


class RoleCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    permissions: List[str] = Field(default_factory=list)


class RolePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    permissions: Optional[List[str]] = None
    version: int = Field(..., description="etag: must match current version")


class AssignIn(BaseModel):
    role_codes: List[str] = Field(..., min_length=1)
    expires_at: Optional[datetime] = Field(
        default=None, description="Time condition: binding ends here (RFC3339)"
    )


class DenyIn(BaseModel):
    permission_code: str = Field(..., min_length=1, max_length=80)
    reason: Optional[str] = Field(default=None, max_length=255)


class DenyOut(BaseModel):
    id: str
    permission_code: str
    reason: Optional[str] = None
    is_active: bool


# ── Helpers ────────────────────────────────────────────────────────────────

async def _role_out(db: AsyncSession, role: Role) -> RoleOut:
    codes = (
        await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
        )
    ).all()
    return RoleOut(
        id=str(role.id),
        code=role.code,
        name=role.name,
        is_system=bool(role.is_system),
        inherit_to_children=bool(getattr(role, "inherit_to_children", False)),
        version=int(role.version or 1),
        permissions=sorted(r[0] for r in codes),
    )


def _forbid_escalation(actor: CurrentPrincipal, codes: List[str]) -> None:
    outside = [c for c in codes if not actor.has_permission(c)]
    if outside:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot grant permissions you do not hold: {', '.join(sorted(outside))}",
        )


async def _assignable_role(
    db: AsyncSession, org_id, role_code: str
) -> Role:
    role = (
        await db.execute(
            select(Role).where(Role.org_id == org_id, Role.code == role_code)
        )
    ).scalars().first()
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role not found: {role_code}")
    return role


# ── Policy reads ───────────────────────────────────────────────────────────

@router.get("/permissions", response_model=List[str])
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.view")),
):
    """Permission catalog (GCP: list supported permissions for custom roles)."""
    rows = (await db.execute(select(Permission.code))).all()
    return sorted({r[0] for r in rows})


@router.get("", response_model=List[RoleOut])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.view")),
):
    roles = (
        await db.execute(
            select(Role)
            .where(Role.org_id == principal.org_id)
            .order_by(Role.name)
        )
    ).scalars().all()
    return [await _role_out(db, r) for r in roles]


# ── Custom roles ───────────────────────────────────────────────────────────

@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    code = data.code.strip().lower().replace(" ", "_")
    if not code:
        raise HTTPException(status_code=422, detail="Role code is required")
    existing = (
        await db.execute(
            select(Role).where(Role.org_id == principal.org_id, Role.code == code)
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Role code already exists")
    known = {
        r[0]
        for r in (
            await db.execute(select(Permission.code))
        ).all()
    }
    unknown = [c for c in data.permissions if c not in known]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown permissions: {', '.join(sorted(unknown))}"
        )
    _forbid_escalation(principal, data.permissions)
    role = Role(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        code=code,
        name=data.name.strip(),
        is_system=False,
        inherit_to_children=False,
        version=1,
    )
    db.add(role)
    await db.flush()
    for perm_code in set(data.permissions):
        perm = (
            await db.execute(select(Permission).where(Permission.code == perm_code))
        ).scalars().first()
        if perm is not None:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    await db.commit()
    await db.refresh(role)
    return await _role_out(db, role)


@router.patch("/{role_id}", response_model=RoleOut)
async def patch_role(
    role_id: str,
    data: RolePatch,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    role = (
        await db.execute(
            select(Role).where(Role.id == role_id, Role.org_id == principal.org_id)
        )
    ).scalars().first()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(
            status_code=403, detail="System roles are immutable (like GCP predefined roles)"
        )
    if int(role.version or 1) != data.version:
        raise HTTPException(
            status_code=409,
            detail="Role changed since you read it — reload and retry (etag mismatch)",
        )
    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Role name is required")
        role.name = name
    if data.permissions is not None:
        known = {r[0] for r in (await db.execute(select(Permission.code))).all()}
        unknown = [c for c in data.permissions if c not in known]
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"Unknown permissions: {', '.join(sorted(unknown))}"
            )
        _forbid_escalation(principal, data.permissions)
        old = (
            await db.execute(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )
        ).scalars().all()
        for row in old:
            await db.delete(row)
        for perm_code in set(data.permissions):
            perm = (
                await db.execute(select(Permission).where(Permission.code == perm_code))
            ).scalars().first()
            if perm is not None:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    role.version = int(role.version or 1) + 1
    await db.commit()
    await db.refresh(role)
    return await _role_out(db, role)


@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    role = (
        await db.execute(
            select(Role).where(Role.id == role_id, Role.org_id == principal.org_id)
        )
    ).scalars().first()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="System roles cannot be deleted")
    await db.delete(role)  # bindings cascade; holders simply lose those grants
    await db.commit()
    return None


# ── Bindings ───────────────────────────────────────────────────────────────

@router.get("/users/{user_id}/roles", response_model=List[str])
async def user_roles(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.view")),
):
    target = (
        await db.execute(
            select(User).where(User.id == user_id, User.org_id == principal.org_id)
        )
    ).scalars().first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    rows = (
        await db.execute(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == target.id, UserRole.org_id == principal.org_id)
        )
    ).all()
    return sorted({r[0] for r in rows})


@router.post("/users/{user_id}/roles", response_model=List[str])
async def assign_roles(
    user_id: str,
    data: AssignIn,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    target = (
        await db.execute(
            select(User).where(User.id == user_id, User.org_id == principal.org_id)
        )
    ).scalars().first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if data.expires_at is not None and data.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    for code in data.role_codes:
        role = await _assignable_role(db, principal.org_id, code.strip())
        perms = {
            r[0]
            for r in (
                await db.execute(
                    select(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id == role.id)
                )
            ).all()
        }
        _forbid_escalation(principal, sorted(perms))
        existing = (
            await db.execute(
                select(UserRole).where(
                    UserRole.user_id == target.id,
                    UserRole.role_id == role.id,
                    UserRole.org_id == principal.org_id,
                )
            )
        ).scalars().first()
        if existing is None:
            db.add(
                UserRole(
                    user_id=target.id,
                    role_id=role.id,
                    org_id=principal.org_id,
                    granted_by=principal.user.id,
                    expires_at=data.expires_at,
                )
            )
    await db.commit()
    return await user_roles(user_id, db, principal)


@router.delete("/users/{user_id}/roles/{role_code}", response_model=List[str])
async def revoke_role(
    user_id: str,
    role_code: str,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    target = (
        await db.execute(
            select(User).where(User.id == user_id, User.org_id == principal.org_id)
        )
    ).scalars().first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    role = await _assignable_role(db, principal.org_id, role_code)
    # GCP last-owner guard: never strand the org without roles.assign holders.
    if str(target.id) == str(principal.user.id):
        remaining = (
            await db.execute(
                select(UserRole).where(
                    UserRole.role_id == role.id,
                    UserRole.org_id == principal.org_id,
                    UserRole.user_id != target.id,
                )
            )
        ).scalars().all()
        if not remaining:
            perms = {
                r[0]
                for r in (
                    await db.execute(
                        select(Permission.code)
                        .join(RolePermission, RolePermission.permission_id == Permission.id)
                        .where(RolePermission.role_id == role.id)
                    )
                ).all()
            }
            if "roles.assign" in perms:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot remove your own last roles.assign grant",
                )
    link = (
        await db.execute(
            select(UserRole).where(
                UserRole.user_id == target.id,
                UserRole.role_id == role.id,
                UserRole.org_id == principal.org_id,
            )
        )
    ).scalars().first()
    if link is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    await db.delete(link)
    await db.commit()
    return await user_roles(user_id, db, principal)


# ── Deny rules ─────────────────────────────────────────────────────────────

@router.get("/deny", response_model=List[DenyOut])
async def list_deny(
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.view")),
):
    rows = (
        await db.execute(
            select(DenyRule).where(DenyRule.org_id == principal.org_id)
        )
    ).scalars().all()
    return [
        DenyOut(
            id=str(r.id),
            permission_code=r.permission_code,
            reason=r.reason,
            is_active=bool(r.is_active),
        )
        for r in rows
    ]


@router.post("/deny", response_model=DenyOut, status_code=201)
async def create_deny(
    data: DenyIn,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    known = {r[0] for r in (await db.execute(select(Permission.code))).all()}
    if data.permission_code not in known:
        raise HTTPException(status_code=422, detail="Unknown permission code")
    rule = DenyRule(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        permission_code=data.permission_code,
        reason=(data.reason or "").strip() or None,
        is_active=True,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return DenyOut(
        id=str(rule.id),
        permission_code=rule.permission_code,
        reason=rule.reason,
        is_active=True,
    )


@router.delete("/deny/{rule_id}", status_code=204)
async def delete_deny(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("roles.assign")),
):
    rule = (
        await db.execute(
            select(DenyRule).where(
                DenyRule.id == rule_id, DenyRule.org_id == principal.org_id
            )
        )
    ).scalars().first()
    if rule is None:
        raise HTTPException(status_code=404, detail="Deny rule not found")
    await db.delete(rule)
    await db.commit()
    return None
