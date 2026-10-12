"""Staff management (Phase 2): invite to SAME org + roles + scopes.

Easy version:
  Owner invites reception@hospital.com as 'staff' -> she gets a link
  -> sets name+password -> joins SAME hospital, no new shop.
  Doctor login links to providers.user_id so he sees only own patients.
  Dept manager gets unit_ids so he sees only his floor (Cardiology).
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.v1.endpoints.auth import deny_impersonation, get_current_user
from app.core.rbac import get_current_principal, require_permission
from app.core.security import hash_password
from app.crud.user import get_user_by_email
from app.db.database import get_db
from app.models.rbac import Role, UserRole
from app.models.service_unit import Provider, ServiceUnit
from app.models.staff import StaffInvite, StaffUnitScope
from app.models.user import User

router = APIRouter()

INVITE_TTL_HOURS = 48
ALLOWED_ROLES = ("owner", "manager", "staff", "viewer")


def _now():
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Schemas ──


class InviteCreate(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    role_code: str = Field(default="staff", max_length=80)
    unit_ids: Optional[List[str]] = None
    provider_id: Optional[str] = None


class InviteOut(BaseModel):
    id: str
    email: str
    role_code: str
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    # Returned once so the owner can share/copy the full link.
    invite_token: Optional[str] = None
    invite_link: Optional[str] = None
    email_sent: bool = False


class InviteAccept(BaseModel):
    token: str = Field(min_length=10)
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=6, max_length=128)


class MemberOut(BaseModel):
    id: str
    email: str
    full_name: str
    roles: List[str] = []
    provider_id: Optional[str] = None
    unit_ids: List[str] = []
    is_active: bool


async def _member_out(
    db: AsyncSession, org_id, u: User, role_codes: List[str]
) -> MemberOut:
    scopes = (
        await db.execute(
            select(StaffUnitScope).where(StaffUnitScope.user_id == u.id)
        )
    ).scalars().all()
    prov = (
        await db.execute(
            select(Provider).where(
                Provider.user_id == u.id, Provider.org_id == org_id
            )
        )
    ).scalars().first()
    return MemberOut(
        id=str(u.id),
        email=u.email,
        full_name=u.full_name,
        roles=role_codes,
        provider_id=str(prov.id) if prov else None,
        unit_ids=[str(s.unit_id) for s in scopes],
        is_active=bool(u.is_active),
    )


# ── Invites ──


def _public_frontend_base(request: Request) -> str:
    """Public browser URL for building clickable invite links.

    Prefers FRONTEND_URL when it is a real https domain, else the request's
    own origin (works behind tunnels/gateway without extra config).
    """
    from app.core.config import settings

    configured = (settings.FRONTEND_URL or "").strip().rstrip("/")
    if configured.startswith("https://") and "localhost" not in configured:
        return configured
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin.startswith(("https://", "http://")):
        return origin
    return configured or "http://localhost:3002"


@router.post("/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    data: InviteCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("users.manage")),
):
    from app.core.org_mode import require_organization_for

    await require_organization_for(db, user)
    email = data.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Valid email is required")
    if data.role_code not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=422, detail=f"role must be one of {ALLOWED_ROLES}"
        )
    if await get_user_by_email(db, email):
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        )
    unit_ids = list(dict.fromkeys(data.unit_ids or []))
    if unit_ids:
        rows = (
            await db.execute(
                select(ServiceUnit).where(
                    ServiceUnit.org_id == user.org_id,
                    ServiceUnit.id.in_(unit_ids),
                )
            )
        ).scalars().all()
        if len(rows) != len(unit_ids):
            raise HTTPException(status_code=404, detail="One or more units not found")
    if data.provider_id:
        prov = (
            await db.execute(
                select(Provider).where(
                    Provider.id == data.provider_id,
                    Provider.org_id == user.org_id,
                )
            )
        ).scalars().first()
        if prov is None:
            raise HTTPException(status_code=404, detail="Provider not found")

    raw = secrets.token_hex(24)
    inv = StaffInvite(
        id=uuid.uuid4(),
        org_id=user.org_id,
        email=email,
        role_code=data.role_code,
        unit_ids=unit_ids or None,
        provider_id=data.provider_id,
        token_hash=_hash(raw),
        expires_at=_now() + timedelta(hours=INVITE_TTL_HOURS),
        created_by=user.id,
        created_at=_now(),
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)

    # Normal-website flow: email the clickable link right away (best-effort;
    # the raw token is still returned so the owner can share it manually).
    from app.models.organization import Organization as _Org

    org = (
        await db.execute(select(_Org).where(_Org.id == user.org_id))
    ).scalars().first()
    link = f"{_public_frontend_base(request)}/accept-invite?token={raw}"
    email_sent = False
    try:
        from app.core.audit import log_audit_event
        from app.core.mail import send_staff_invite_email
        from app.schemas.audit import AuditAction

        background_tasks.add_task(
            send_staff_invite_email,
            email,
            getattr(org, "name", "your organization") if org else "your organization",
            data.role_code,
            link,
        )
        email_sent = True
        log_audit_event(
            background_tasks,
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            action=AuditAction.STAFF_INVITE,
            resource_type="staff_invite",
            resource_id=str(inv.id),
            old_values=None,
            new_values={"email": email, "role": data.role_code},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except Exception:
        email_sent = False

    return InviteOut(
        id=str(inv.id),
        email=inv.email,
        role_code=inv.role_code,
        expires_at=inv.expires_at,
        invite_token=raw,
        invite_link=link,
        email_sent=email_sent,
    )


@router.get("/invites", response_model=List[InviteOut])
async def list_invites(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _perm: None = Depends(require_permission("users.view")),
):
    rows = (
        await db.execute(
            select(StaffInvite)
            .where(StaffInvite.org_id == user.org_id)
            .order_by(StaffInvite.created_at.desc())
        )
    ).scalars().all()
    return [
        InviteOut(
            id=str(r.id),
            email=r.email,
            role_code=r.role_code,
            expires_at=r.expires_at,
            accepted_at=r.accepted_at,
        )
        for r in rows
    ]


@router.post("/accept")
async def accept_invite(
    data: InviteAccept,
    db: AsyncSession = Depends(get_db),
):
    inv = (
        await db.execute(
            select(StaffInvite).where(
                StaffInvite.token_hash == _hash(data.token.strip())
            )
        )
    ).scalars().first()
    if inv is None or inv.accepted_at is not None or inv.expires_at < _now():
        raise HTTPException(status_code=401, detail="Invalid or expired invite")
    if await get_user_by_email(db, inv.email):
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        )
    nu = User(
        id=uuid.uuid4(),
        email=inv.email,
        hashed_password=hash_password(data.password.strip()),
        full_name=data.full_name.strip(),
        org_id=inv.org_id,
        is_active=True,
    )
    if not nu.full_name:
        raise HTTPException(status_code=422, detail="Name is required")
    db.add(nu)
    await db.flush()

    role = (
        await db.execute(
            select(Role).where(
                Role.org_id == inv.org_id, Role.code == inv.role_code
            )
        )
    ).scalars().first()
    if role is not None:
        db.add(UserRole(user_id=nu.id, role_id=role.id, org_id=inv.org_id))
    for uid in inv.unit_ids or []:
        db.add(
            StaffUnitScope(org_id=inv.org_id, user_id=nu.id, unit_id=uid)
        )
    if inv.provider_id:
        prov = (
            await db.execute(
                select(Provider).where(Provider.id == inv.provider_id)
            )
        ).scalars().first()
        if prov is not None:
            prov.user_id = nu.id

    inv.accepted_at = _now()
    await db.commit()
    return {"detail": "Account created. You can now sign in.", "email": nu.email}


# ── Members ──


@router.get("/members", response_model=List[MemberOut])
async def list_members(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _perm: None = Depends(require_permission("users.view")),
):
    users = (
        await db.execute(
            select(User)
            .where(User.org_id == user.org_id)
            .order_by(User.created_at)
        )
    ).scalars().all()
    out = []
    for u in users:
        codes = [
            r[0]
            for r in (
                await db.execute(
                    select(Role.code)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == u.id)
                )
            ).all()
        ]
        out.append(await _member_out(db, user.org_id, u, sorted(set(codes))))
    return out


@router.get("/me/scope")
async def my_scope(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    principal=Depends(get_current_principal),
):
    """What can I see? provider + departments (names included for UI badges)."""
    scopes = (
        await db.execute(
            select(StaffUnitScope).where(StaffUnitScope.user_id == user.id)
        )
    ).scalars().all()
    prov = (
        await db.execute(
            select(Provider).where(
                Provider.user_id == user.id, Provider.org_id == user.org_id
            )
        )
    ).scalars().first()
    unit_ids = [str(s.unit_id) for s in scopes]
    unit_names: list[str] = []
    provider_unit_names: list[str] = []
    try:
        from app.models.service_unit import ServiceUnit, UnitProvider

        if unit_ids:
            unit_names = [
                r.name
                for r in (
                    await db.execute(
                        select(ServiceUnit).where(ServiceUnit.id.in_([s.unit_id for s in scopes]))
                    )
                ).scalars().all()
            ]
        if prov is not None:
            _links = (
                await db.execute(
                    select(UnitProvider).where(UnitProvider.provider_id == prov.id)
                )
            ).scalars().all()
            if _links:
                provider_unit_names = [
                    r.name
                    for r in (
                        await db.execute(
                            select(ServiceUnit).where(
                                ServiceUnit.id.in_([a.unit_id for a in _links])
                            )
                        )
                    ).scalars().all()
                ]
    except Exception:
        unit_names = []
        provider_unit_names = []
    return {
        "org_type": getattr(principal.org, "type", "individual"),
        "roles": principal.role_codes,
        "provider_id": str(prov.id) if prov else None,
        "provider_name": getattr(prov, "full_name", None),
        "unit_ids": unit_ids,
        "unit_names": unit_names,
        "provider_unit_names": provider_unit_names,
    }
