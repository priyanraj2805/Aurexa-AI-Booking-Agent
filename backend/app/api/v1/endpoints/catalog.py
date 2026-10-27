"""Service catalog CRUD (generic Units / Providers / Fees).

Org-admin surface for ANY booking business: units are departments/branches/
lanes, providers are doctors/stylists/coaches. All isolation derives
``org_id`` from the authenticated user; mutating ops additionally refuse
impersonated sessions (same convention as documents.py).
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.v1.endpoints.auth import deny_impersonation, get_current_user
from app.core.rbac import require_permission
from app.db.database import get_db
from app.models.service_unit import Provider, ProviderFee, ServiceUnit, UnitProvider
from app.models.user import User

# Pilot RBAC surface: every catalog route requires at least catalog.view
# (DB-loaded per request); mutating routes additionally require
# catalog.manage. Other dashboard routes keep legacy any-active-user access
# until they are migrated one surface at a time.
async def _require_organization(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Catalog is an organization-only surface (individuals: 403)."""
    from app.core.org_mode import require_organization_for

    await require_organization_for(db, user)


router = APIRouter(
    dependencies=[
        Depends(require_permission("catalog.view")),
        Depends(_require_organization),
    ]
)


# ── Schemas ────────────────────────────────────────────────────────────────

class UnitCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True


class UnitUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


class UnitOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    is_active: bool
    providers_count: int = 0


class ProviderCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    role_label: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=50)
    is_active: bool = True
    timezone: Optional[str] = Field(default=None, max_length=64)
    slot_duration_minutes: Optional[int] = Field(default=None, gt=0, le=480)
    booking_window_days: Optional[int] = Field(default=None, gt=0, le=365)


class ProviderUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    role_label: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=50)
    is_active: Optional[bool] = None
    timezone: Optional[str] = Field(default=None, max_length=64)
    slot_duration_minutes: Optional[int] = Field(default=None, gt=0, le=480)
    booking_window_days: Optional[int] = Field(default=None, gt=0, le=365)


class ProviderOut(BaseModel):
    id: str
    full_name: str
    role_label: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool
    units: List[str] = []
    timezone: Optional[str] = None
    slot_duration_minutes: Optional[int] = None
    booking_window_days: Optional[int] = None


class AssignIn(BaseModel):
    provider_id: str
    is_primary: bool = False


class FeeCreate(BaseModel):
    unit_id: Optional[str] = None
    provider_id: Optional[str] = None
    amount_paise: int = Field(..., gt=0)
    currency: str = Field(default="INR", max_length=10)
    label: Optional[str] = Field(default=None, max_length=120)
    is_active: bool = True


class FeeUpdate(BaseModel):
    amount_paise: Optional[int] = Field(default=None, gt=0)
    currency: Optional[str] = Field(default=None, max_length=10)
    label: Optional[str] = Field(default=None, max_length=120)
    is_active: Optional[bool] = None


class FeeOut(BaseModel):
    id: str
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None
    amount_paise: int
    currency: str
    label: Optional[str] = None
    is_active: bool


# ── Helpers ────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


async def _get_unit(db: AsyncSession, org_id, unit_id: str) -> ServiceUnit:
    unit = (
        await db.execute(
            select(ServiceUnit).where(
                ServiceUnit.id == unit_id, ServiceUnit.org_id == org_id
            )
        )
    ).scalars().first()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    return unit


async def _get_provider(db: AsyncSession, org_id, provider_id: str) -> Provider:
    provider = (
        await db.execute(
            select(Provider).where(
                Provider.id == provider_id, Provider.org_id == org_id
            )
        )
    ).scalars().first()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


# ── Units ──────────────────────────────────────────────────────────────────

@router.get("/units", response_model=List[UnitOut])
async def list_units(
    include_inactive: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(ServiceUnit).where(ServiceUnit.org_id == user.org_id)
    if not include_inactive:
        q = q.where(ServiceUnit.is_active.is_(True))
    rows = (await db.execute(q.order_by(ServiceUnit.name))).scalars().all()
    out = []
    for u in rows:
        n = (
            await db.execute(
                select(UnitProvider).where(UnitProvider.unit_id == u.id)
            )
        )
        count = len(n.scalars().all())
        out.append(
            UnitOut(
                id=str(u.id),
                name=u.name,
                description=u.description,
                is_active=bool(u.is_active),
                providers_count=count,
            )
        )
    return out


@router.post("/units", response_model=UnitOut, status_code=201)
async def create_unit(
    data: UnitCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    unit = ServiceUnit(
        id=uuid.uuid4(),
        org_id=user.org_id,
        name=data.name.strip(),
        description=(data.description or "").strip() or None,
        is_active=data.is_active,
        created_at=_now(),
        updated_at=_now(),
    )
    if not unit.name:
        raise HTTPException(status_code=422, detail="Unit name is required")
    db.add(unit)
    await db.commit()
    await db.refresh(unit)
    return UnitOut(
        id=str(unit.id),
        name=unit.name,
        description=unit.description,
        is_active=bool(unit.is_active),
        providers_count=0,
    )


@router.get("/units/{unit_id}", response_model=UnitOut)
async def get_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    unit = await _get_unit(db, user.org_id, unit_id)
    n = (
        await db.execute(
            select(UnitProvider).where(UnitProvider.unit_id == unit.id)
        )
    )
    return UnitOut(
        id=str(unit.id),
        name=unit.name,
        description=unit.description,
        is_active=bool(unit.is_active),
        providers_count=len(n.scalars().all()),
    )


@router.patch("/units/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: str,
    data: UnitUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    unit = await _get_unit(db, user.org_id, unit_id)
    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Unit name is required")
        unit.name = name
    if data.description is not None:
        unit.description = data.description.strip() or None
    if data.is_active is not None:
        unit.is_active = bool(data.is_active)
    unit.updated_at = _now()
    await db.commit()
    await db.refresh(unit)
    return await get_unit(unit_id, db, user)


@router.delete("/units/{unit_id}", status_code=204)
async def delete_unit(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    unit = await _get_unit(db, user.org_id, unit_id)
    await db.delete(unit)
    await db.commit()
    return None


# ── Providers ──────────────────────────────────────────────────────────────

@router.get("/providers", response_model=List[ProviderOut])
async def list_providers(
    include_inactive: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Provider).where(Provider.org_id == user.org_id)
    if not include_inactive:
        q = q.where(Provider.is_active.is_(True))
    rows = (await db.execute(q.order_by(Provider.full_name))).scalars().all()
    out = []
    for p in rows:
        links = (
            await db.execute(
                select(UnitProvider).where(UnitProvider.provider_id == p.id)
            )
        ).scalars().all()
        out.append(
            ProviderOut(
                id=str(p.id),
                full_name=p.full_name,
                role_label=p.role_label,
                email=p.email,
                phone=p.phone,
                is_active=bool(p.is_active),
                units=[str(a.unit_id) for a in links],
                timezone=getattr(p, "timezone", None),
                slot_duration_minutes=getattr(p, "slot_duration_minutes", None),
                booking_window_days=getattr(p, "booking_window_days", None),
            )
        )
    return out


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    data: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    provider = Provider(
        id=uuid.uuid4(),
        org_id=user.org_id,
        full_name=data.full_name.strip(),
        role_label=(data.role_label or "").strip() or None,
        email=(data.email or "").strip() or None,
        phone=(data.phone or "").strip() or None,
        is_active=data.is_active,
        timezone=(data.timezone or "").strip() or None,
        slot_duration_minutes=data.slot_duration_minutes,
        booking_window_days=data.booking_window_days,
        created_at=_now(),
        updated_at=_now(),
    )
    if not provider.full_name:
        raise HTTPException(status_code=422, detail="Provider name is required")
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return ProviderOut(
        id=str(provider.id),
        full_name=provider.full_name,
        role_label=provider.role_label,
        email=provider.email,
        phone=provider.phone,
        is_active=bool(provider.is_active),
        units=[],
        timezone=provider.timezone,
        slot_duration_minutes=provider.slot_duration_minutes,
        booking_window_days=provider.booking_window_days,
    )


@router.get("/providers/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    provider = await _get_provider(db, user.org_id, provider_id)
    links = (
        await db.execute(
            select(UnitProvider).where(UnitProvider.provider_id == provider.id)
        )
    ).scalars().all()
    return ProviderOut(
        id=str(provider.id),
        full_name=provider.full_name,
        role_label=provider.role_label,
        email=provider.email,
        phone=provider.phone,
        is_active=bool(provider.is_active),
        units=[str(a.unit_id) for a in links],
        timezone=getattr(provider, "timezone", None),
        slot_duration_minutes=getattr(provider, "slot_duration_minutes", None),
        booking_window_days=getattr(provider, "booking_window_days", None),
    )


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    data: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    provider = await _get_provider(db, user.org_id, provider_id)
    if data.full_name is not None:
        name = data.full_name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Provider name is required")
        provider.full_name = name
    if data.role_label is not None:
        provider.role_label = data.role_label.strip() or None
    if data.email is not None:
        provider.email = data.email.strip() or None
    if data.phone is not None:
        provider.phone = data.phone.strip() or None
    if data.is_active is not None:
        provider.is_active = bool(data.is_active)
    if data.timezone is not None:
        provider.timezone = data.timezone.strip() or None
    if data.slot_duration_minutes is not None:
        provider.slot_duration_minutes = data.slot_duration_minutes
    if data.booking_window_days is not None:
        provider.booking_window_days = data.booking_window_days
    provider.updated_at = _now()
    await db.commit()
    await db.refresh(provider)
    return await get_provider(provider_id, db, user)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    provider = await _get_provider(db, user.org_id, provider_id)
    await db.delete(provider)
    await db.commit()
    return None


# ── Assignments ────────────────────────────────────────────────────────────

@router.get("/units/{unit_id}/providers", response_model=List[ProviderOut])
async def unit_providers(
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    unit = await _get_unit(db, user.org_id, unit_id)
    links = (
        await db.execute(
            select(UnitProvider).where(UnitProvider.unit_id == unit.id)
        )
    ).scalars().all()
    out = []
    for a in links:
        p = await _get_provider(db, user.org_id, str(a.provider_id))
        out.append(
            ProviderOut(
                id=str(p.id),
                full_name=p.full_name,
                role_label=p.role_label,
                email=p.email,
                phone=p.phone,
                is_active=bool(p.is_active),
                units=[str(unit.id)],
                timezone=getattr(p, "timezone", None),
                slot_duration_minutes=getattr(p, "slot_duration_minutes", None),
                booking_window_days=getattr(p, "booking_window_days", None),
            )
        )
    return out


@router.post("/units/{unit_id}/providers", status_code=201)
async def assign_provider(
    unit_id: str,
    data: AssignIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    unit = await _get_unit(db, user.org_id, unit_id)
    provider = await _get_provider(db, user.org_id, data.provider_id)
    existing = (
        await db.execute(
            select(UnitProvider).where(
                UnitProvider.unit_id == unit.id,
                UnitProvider.provider_id == provider.id,
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Provider already assigned to this unit")
    link = UnitProvider(
        id=uuid.uuid4(),
        org_id=user.org_id,
        unit_id=unit.id,
        provider_id=provider.id,
        is_primary=bool(data.is_primary),
        created_at=_now(),
    )
    db.add(link)
    await db.commit()
    return {"unit_id": str(unit.id), "provider_id": str(provider.id)}


@router.delete("/units/{unit_id}/providers/{provider_id}", status_code=204)
async def unassign_provider(
    unit_id: str,
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    unit = await _get_unit(db, user.org_id, unit_id)
    provider = await _get_provider(db, user.org_id, provider_id)
    link = (
        await db.execute(
            select(UnitProvider).where(
                UnitProvider.unit_id == unit.id,
                UnitProvider.provider_id == provider.id,
            )
        )
    ).scalars().first()
    if link is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await db.delete(link)
    await db.commit()
    return None


# ── Fees ───────────────────────────────────────────────────────────────────

async def _fee_out(db: AsyncSession, fee: ProviderFee) -> FeeOut:
    unit_name = None
    if fee.unit_id is not None:
        u = (
            await db.execute(select(ServiceUnit).where(ServiceUnit.id == fee.unit_id))
        ).scalars().first()
        unit_name = u.name if u else None
    provider_name = None
    if fee.provider_id is not None:
        p = (
            await db.execute(select(Provider).where(Provider.id == fee.provider_id))
        ).scalars().first()
        provider_name = p.full_name if p else None
    return FeeOut(
        id=str(fee.id),
        unit_id=str(fee.unit_id) if fee.unit_id else None,
        unit_name=unit_name,
        provider_id=str(fee.provider_id) if fee.provider_id else None,
        provider_name=provider_name,
        amount_paise=fee.amount_paise,
        currency=fee.currency,
        label=fee.label,
        is_active=bool(fee.is_active),
    )


@router.get("/fees", response_model=List[FeeOut])
async def list_fees(
    unit_id: Optional[str] = Query(default=None),
    provider_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(ProviderFee).where(ProviderFee.org_id == user.org_id)
    if unit_id:
        q = q.where(ProviderFee.unit_id == unit_id)
    if provider_id:
        q = q.where(ProviderFee.provider_id == provider_id)
    rows = (await db.execute(q.order_by(ProviderFee.created_at))).scalars().all()
    return [await _fee_out(db, f) for f in rows]


@router.post("/fees", response_model=FeeOut, status_code=201)
async def create_fee(
    data: FeeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    if not data.unit_id and not data.provider_id:
        raise HTTPException(
            status_code=422, detail="Fee needs a unit, a provider, or both"
        )
    if data.unit_id:
        await _get_unit(db, user.org_id, data.unit_id)
    if data.provider_id:
        await _get_provider(db, user.org_id, data.provider_id)
    fee = ProviderFee(
        id=uuid.uuid4(),
        org_id=user.org_id,
        unit_id=data.unit_id,
        provider_id=data.provider_id,
        amount_paise=data.amount_paise,
        currency=(data.currency or "INR").upper(),
        label=(data.label or "").strip() or None,
        is_active=data.is_active,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(fee)
    await db.commit()
    await db.refresh(fee)
    return await _fee_out(db, fee)


@router.patch("/fees/{fee_id}", response_model=FeeOut)
async def update_fee(
    fee_id: str,
    data: FeeUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    fee = (
        await db.execute(
            select(ProviderFee).where(
                ProviderFee.id == fee_id, ProviderFee.org_id == user.org_id
            )
        )
    ).scalars().first()
    if fee is None:
        raise HTTPException(status_code=404, detail="Fee not found")
    if data.amount_paise is not None:
        fee.amount_paise = data.amount_paise
    if data.currency is not None:
        fee.currency = data.currency.upper()
    if data.label is not None:
        fee.label = data.label.strip() or None
    if data.is_active is not None:
        fee.is_active = bool(data.is_active)
    fee.updated_at = _now()
    await db.commit()
    await db.refresh(fee)
    return await _fee_out(db, fee)


class ProviderDayIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    is_active: bool = True
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    break_start: Optional[str] = None
    break_end: Optional[str] = None


class ProviderDayOut(BaseModel):
    day_of_week: int
    is_active: bool
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    break_start: Optional[str] = None
    break_end: Optional[str] = None
    is_override: bool = False


class ProviderExceptionIn(BaseModel):
    exception_date: str = Field(min_length=10, max_length=10)
    exception_type: str = Field(default="CLOSED", max_length=20)
    custom_open: Optional[str] = None
    custom_close: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=255)


class ProviderExceptionOut(BaseModel):
    id: str
    exception_date: str
    exception_type: str
    custom_open: Optional[str] = None
    custom_close: Optional[str] = None
    reason: Optional[str] = None


@router.get("/providers/{provider_id}/schedule", response_model=List[ProviderDayOut])
async def get_provider_schedule(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Weekly hours for one doctor. is_override=False rows come from org template."""
    from app.models.booking import ScheduleDay as _Day

    provider = await _get_provider(db, user.org_id, provider_id)
    out: List[ProviderDayOut] = []
    for dow in range(7):
        override = (
            await db.execute(
                select(_Day).where(
                    _Day.org_id == user.org_id,
                    _Day.day_of_week == dow,
                    _Day.provider_id == provider.id,
                )
            )
        ).scalars().first()
        if override is not None:
            out.append(
                ProviderDayOut(
                    day_of_week=dow,
                    is_active=bool(override.is_active),
                    open_time=str(override.open_time) if override.open_time else None,
                    close_time=str(override.close_time) if override.close_time else None,
                    break_start=str(override.break_start) if override.break_start else None,
                    break_end=str(override.break_end) if override.break_end else None,
                    is_override=True,
                )
            )
            continue
        base = (
            await db.execute(
                select(_Day).where(
                    _Day.org_id == user.org_id,
                    _Day.day_of_week == dow,
                )
            )
        ).scalars().first()
        # Prefer org template (provider NULL) when present
        if base is not None and getattr(base, "provider_id", None) is not None:
            try:
                rows = (
                    await db.execute(
                        select(_Day).where(
                            _Day.org_id == user.org_id,
                            _Day.day_of_week == dow,
                        )
                    )
                ).scalars().all()
                base = next(
                    (r for r in rows if getattr(r, "provider_id", None) is None),
                    base,
                )
            except Exception:
                pass
        out.append(
            ProviderDayOut(
                day_of_week=dow,
                is_active=bool(base.is_active) if base else False,
                open_time=str(base.open_time) if base and base.open_time else None,
                close_time=str(base.close_time) if base and base.close_time else None,
                break_start=str(base.break_start) if base and base.break_start else None,
                break_end=str(base.break_end) if base and base.break_end else None,
                is_override=False,
            )
        )
    return out


@router.put("/providers/{provider_id}/schedule", response_model=List[ProviderDayOut])
async def save_provider_schedule(
    provider_id: str,
    days: List[ProviderDayIn],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    """Replace this doctor's weekly override. Empty list clears overrides."""
    import uuid as _uuid
    from app.models.booking import ScheduleDay as _Day

    provider = await _get_provider(db, user.org_id, provider_id)
    seen = set()
    for d in days:
        if d.day_of_week in seen:
            raise HTTPException(status_code=422, detail="Duplicate day_of_week")
        seen.add(d.day_of_week)

    for d in days:
        row = (
            await db.execute(
                select(_Day).where(
                    _Day.org_id == user.org_id,
                    _Day.day_of_week == d.day_of_week,
                    _Day.provider_id == provider.id,
                )
            )
        ).scalars().first()
        if row is None:
            row = _Day(
                id=_uuid.uuid4(),
                org_id=user.org_id,
                provider_id=provider.id,
                day_of_week=d.day_of_week,
            )
            db.add(row)
        row.is_active = bool(d.is_active)
        from datetime import time as _time

        def _parse(t):
            if not t:
                return None
            t = str(t).strip()
            try:
                hh, mm = t.split(":")[:2]
                return _time(int(hh), int(mm))
            except Exception:
                raise HTTPException(
                    status_code=422, detail=f"Invalid time '{t}', use HH:MM"
                )

        row.open_time = _parse(d.open_time) if d.is_active else None
        row.close_time = _parse(d.close_time) if d.is_active else None
        row.break_start = _parse(d.break_start) if d.is_active else None
        row.break_end = _parse(d.break_end) if d.is_active else None
    await db.commit()
    return await get_provider_schedule(provider_id, db, user)


def _prov_time_str(v) -> Optional[str]:
    return str(v) if v else None


@router.get(
    "/providers/{provider_id}/exceptions", response_model=List[ProviderExceptionOut]
)
async def list_provider_exceptions(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Off-days / custom hours for one doctor (doctor-specific only)."""
    from app.models.booking import ScheduleException as _SE

    provider = await _get_provider(db, user.org_id, provider_id)
    rows = (
        await db.execute(
            select(_SE)
            .where(
                _SE.org_id == user.org_id,
                _SE.provider_id == provider.id,
            )
            .order_by(_SE.exception_date)
        )
    ).scalars().all()
    return [
        ProviderExceptionOut(
            id=str(r.id),
            exception_date=r.exception_date.isoformat(),
            exception_type=str(r.exception_type.value if hasattr(r.exception_type, "value") else r.exception_type),
            custom_open=_prov_time_str(r.custom_open),
            custom_close=_prov_time_str(r.custom_close),
            reason=r.reason,
        )
        for r in rows
    ]


@router.post(
    "/providers/{provider_id}/exceptions",
    response_model=ProviderExceptionOut,
    status_code=201,
)
async def create_provider_exception(
    provider_id: str,
    data: ProviderExceptionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    """Add an off-day / custom-hours override for one doctor."""
    import uuid as _uuid
    from datetime import date as _date
    from datetime import time as _time
    from app.models.booking import ExceptionType as _ET
    from app.models.booking import ScheduleException as _SE

    provider = await _get_provider(db, user.org_id, provider_id)
    try:
        exc_date = _date.fromisoformat(data.exception_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="exception_date must be YYYY-MM-DD")

    etype = (data.exception_type or "CLOSED").strip().upper()
    if etype not in ("CLOSED", "CUSTOM_HOURS"):
        raise HTTPException(status_code=422, detail="exception_type must be CLOSED or CUSTOM_HOURS")

    def _parse(t):
        if not t:
            return None
        try:
            hh, mm = str(t).strip().split(":")[:2]
            return _time(int(hh), int(mm))
        except Exception:
            raise HTTPException(status_code=422, detail=f"Invalid time '{t}', use HH:MM")

    if etype == "CUSTOM_HOURS" and (not data.custom_open or not data.custom_close):
        raise HTTPException(status_code=422, detail="Custom hours need open + close")

    old = (
        await db.execute(
            select(_SE).where(
                _SE.org_id == user.org_id,
                _SE.exception_date == exc_date,
                _SE.provider_id == provider.id,
            )
        )
    ).scalars().first()
    if old is not None:
        await db.delete(old)
        await db.flush()

    row = _SE(
        id=_uuid.uuid4(),
        org_id=user.org_id,
        provider_id=provider.id,
        exception_date=exc_date,
        exception_type=_ET(etype),
        custom_open=_parse(data.custom_open) if etype == "CUSTOM_HOURS" else None,
        custom_close=_parse(data.custom_close) if etype == "CUSTOM_HOURS" else None,
        reason=(data.reason or "").strip() or None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    rows = await list_provider_exceptions(provider_id, db, user)
    return next(r for r in rows if r.id == str(row.id))


@router.delete("/providers/{provider_id}/exceptions/{exception_id}", status_code=204)
async def delete_provider_exception(
    provider_id: str,
    exception_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    """Delete one doctor's off-day override."""
    import uuid as _uuid
    from app.models.booking import ScheduleException as _SE

    provider = await _get_provider(db, user.org_id, provider_id)
    row = (
        await db.execute(
            select(_SE).where(
                _SE.id == _uuid.UUID(str(exception_id)),
                _SE.org_id == user.org_id,
                _SE.provider_id == provider.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Exception not found")
    await db.delete(row)
    await db.commit()
    return None


@router.get("/providers/{provider_id}/schedule/preview")
async def preview_provider_schedule(
    provider_id: str,
    date: str = Query(default=None, description="Start date YYYY-MM-DD"),
    days: int = Query(default=7, ge=1, le=28),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Slot preview for one doctor — same engine as the business-hours preview."""
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from app.engine.slot_engine import compute_slot_inventory

    provider = await _get_provider(db, user.org_id, provider_id)
    # Resolve effective business timezone (org schedule → doctor override) for local-time slot display
    from zoneinfo import ZoneInfo as _ZI

    _tz_str = "UTC"
    try:
        _sched = (
            await db.execute(
                select(BusinessSchedule).where(BusinessSchedule.org_id == user.org_id)
            )
        ).scalars().first()
        _tz_str = (getattr(_sched, "timezone", None) or "UTC").strip() if _sched is not None else "UTC"
    except Exception:
        _tz_str = "UTC"
    if provider is not None and getattr(provider, "timezone", None):
        try:
            from app.core.org_mode import effective_timezone as _eff_tz

            _tz_str = _eff_tz(provider, _tz_str)
        except Exception:
            pass
    try:
        _local_zone = _ZI((_tz_str or "UTC").strip())
    except Exception:
        _local_zone = _ZI("UTC")

    try:
        start = _date.fromisoformat(date) if date else _dt.now().date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    out = []
    for i in range(days):
        day = start + _td(days=i)
        inv, status = await compute_slot_inventory(
            db, user.org_id, day, org_config=None, provider_id=provider.id
        )
        out.append(
            {
                "date": day.isoformat(),
                "status": status,
                "available_times": sorted(
                    {
                        item["start_utc"].astimezone(_local_zone).strftime("%I:%M %p").lstrip("0")
                        for item in (inv or [])
                        if item.get("available")
                    }
                ),
            }
        )
    return out


@router.delete("/fees/{fee_id}", status_code=204)
async def delete_fee(
    fee_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    _perm: None = Depends(require_permission("catalog.manage")),
):
    fee = (
        await db.execute(
            select(ProviderFee).where(
                ProviderFee.id == fee_id, ProviderFee.org_id == user.org_id
            )
        )
    ).scalars().first()
    if fee is None:
        raise HTTPException(status_code=404, detail="Fee not found")
    await db.delete(fee)
    await db.commit()
    return None
