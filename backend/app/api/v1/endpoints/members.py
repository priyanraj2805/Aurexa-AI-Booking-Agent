"""Org-level Users & Members (providers with own login).

Users share the same admin-panel — permissions gate what they see.
Members are providers linked to a login (providers.user_id) — own dashboard
filtered to their provider_id, no users/roles/billing controls.
"""

import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.rbac import CurrentPrincipal, get_current_principal, require_permission, ensure_org_roles
from app.core.security import hash_password
from app.db.database import get_db
from app.models.rbac import Role, UserRole
from app.models.service_unit import Provider
from app.models.user import User

router = APIRouter()

# ── Schemas ──
class InviteUserIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    role_codes: List[str] = Field(default_factory=lambda: ["manager"])

class InviteMemberIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=50)
    role_label: Optional[str] = Field(default=None, max_length=120)
    role_codes: List[str] = Field(default_factory=lambda: ["staff"])
    unit_ids: List[str] = Field(default_factory=list)

class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    role_codes: List[str] = []
    provider_id: Optional[str] = None

class MemberOut(BaseModel):
    provider_id: str
    user_id: Optional[str] = None
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role_label: Optional[str] = None
    is_active: bool
    role_codes: List[str] = []

def _validate_role_codes(codes: List[str]) -> List[str]:
    out = []
    for c in codes:
        cc = c.strip().lower().replace(" ", "_")
        if cc:
            out.append(cc)
    if not out:
        out = ["staff"]
    return out

async def _assign_roles(db: AsyncSession, principal: CurrentPrincipal, user: User, role_codes: List[str]):
    await ensure_org_roles(db, principal.org)
    for code in role_codes:
        role = (await db.execute(select(Role).where(Role.org_id == principal.org_id, Role.code == code))).scalars().first()
        if not role:
            raise HTTPException(status_code=404, detail=f"Role not found: {code}")
        # escalation check — caller must hold all perms of the role
        perms = {r[0] for r in (await db.execute(select(Role.id).where(Role.id==role.id))).all()}
        # reuse central check via principal.has_permission on each role's perms
        from sqlalchemy import select as sel
        from app.models.rbac import RolePermission, Permission
        perms = {r[0] for r in (await db.execute(select(Permission.code).join(RolePermission, RolePermission.permission_id==Permission.id).where(RolePermission.role_id==role.id))).all()}
        outside = [p for p in perms if not principal.has_permission(p)]
        if outside:
            raise HTTPException(status_code=403, detail=f"Cannot grant permissions you don't hold: {', '.join(outside)}")
        existing = (await db.execute(select(UserRole).where(UserRole.user_id==user.id, UserRole.role_id==role.id, UserRole.org_id==principal.org_id))).scalars().first()
        if not existing:
            db.add(UserRole(user_id=user.id, role_id=role.id, org_id=principal.org_id, granted_by=principal.user.id))
    await db.commit()

async def _user_roles(db: AsyncSession, user: User, org_id) -> List[str]:
    rows = (await db.execute(select(Role.code).join(UserRole, UserRole.role_id==Role.id).where(UserRole.user_id==user.id, UserRole.org_id==org_id))).all()
    return sorted({r[0] for r in rows})

# ── Users (same admin-panel, permission-gated) ──
@router.get("/users", response_model=List[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("users.view")),
):
    users = (await db.execute(select(User).where(User.org_id==principal.org_id).order_by(User.created_at))).scalars().all()
    # map provider link
    prov_by_user = {str(p.user_id): str(p.id) for p in (await db.execute(select(Provider).where(Provider.org_id==principal.org_id, Provider.user_id.is_not(None)))).scalars().all() if p.user_id}
    out = []
    for u in users:
        out.append(UserOut(id=str(u.id), email=u.email, full_name=u.full_name, is_active=bool(u.is_active), role_codes=await _user_roles(db, u, principal.org_id), provider_id=prov_by_user.get(str(u.id))))
    return out

@router.post("/users", response_model=UserOut, status_code=201)
async def invite_user(
    data: InviteUserIn,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("users.manage")),
):
    # also needs roles.assign to bind roles
    if not principal.has_permission("roles.assign"):
        raise HTTPException(status_code=403, detail="Missing permission: roles.assign")
    email = data.email.lower().strip()
    exists = (await db.execute(select(User).where(User.email==email))).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(id=uuid.uuid4(), email=email, hashed_password=hash_password(data.password), full_name=data.full_name.strip(), org_id=principal.org_id, is_active=True)
    db.add(user)
    await db.flush()
    await _assign_roles(db, principal, user, _validate_role_codes(data.role_codes))
    return UserOut(id=str(user.id), email=user.email, full_name=user.full_name, is_active=True, role_codes=await _user_roles(db, user, principal.org_id))

# ── Members (providers with own login + own dashboard) ──
@router.get("/members", response_model=List[MemberOut])
async def list_members(
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("users.view")),
):
    providers = (await db.execute(select(Provider).where(Provider.org_id==principal.org_id).order_by(Provider.full_name))).scalars().all()
    out = []
    for p in providers:
        role_codes = []
        if p.user_id:
            u = (await db.execute(select(User).where(User.id==p.user_id))).scalars().first()
            if u:
                role_codes = await _user_roles(db, u, principal.org_id)
        out.append(MemberOut(provider_id=str(p.id), user_id=str(p.user_id) if p.user_id else None, full_name=p.full_name, email=p.email, phone=p.phone, role_label=p.role_label, is_active=bool(p.is_active), role_codes=role_codes))
    return out

@router.post("/members", response_model=MemberOut, status_code=201)
async def invite_member(
    data: InviteMemberIn,
    db: AsyncSession = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("users.manage")),
):
    if not principal.has_permission("roles.assign"):
        raise HTTPException(status_code=403, detail="Missing permission: roles.assign")
    email = data.email.lower().strip()
    # create user
    exists = (await db.execute(select(User).where(User.email==email))).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(id=uuid.uuid4(), email=email, hashed_password=hash_password(data.password), full_name=data.full_name.strip(), org_id=principal.org_id, is_active=True)
    db.add(user)
    await db.flush()
    # create provider linked to user
    provider = Provider(id=uuid.uuid4(), org_id=principal.org_id, user_id=user.id, full_name=data.full_name.strip(), role_label=(data.role_label or "Member"), email=email, phone=(data.phone or None), is_active=True)
    db.add(provider)
    await db.flush()
    # unit assignments if any
    if data.unit_ids:
        from app.models.service_unit import ServiceUnit, UnitProvider
        for uid in data.unit_ids:
            try:
                u = (await db.execute(select(ServiceUnit).where(ServiceUnit.id==uuid.UUID(uid), ServiceUnit.org_id==principal.org_id))).scalars().first()
            except Exception:
                continue
            if u:
                db.add(UnitProvider(id=uuid.uuid4(), org_id=principal.org_id, unit_id=u.id, provider_id=provider.id))
    await _assign_roles(db, principal, user, _validate_role_codes(data.role_codes))
    # role fetch for response
    role_codes = await _user_roles(db, user, principal.org_id)
    await db.commit()
    await db.refresh(provider)
    return MemberOut(provider_id=str(provider.id), user_id=str(user.id), full_name=provider.full_name, email=provider.email, phone=provider.phone, role_label=provider.role_label, is_active=True, role_codes=role_codes)
