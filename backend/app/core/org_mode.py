"""Organization mode helpers (individual shop vs hospital mall).

Individual orgs (type='individual') keep the old behavior:
  org-level hours/timezone, bookings without unit/provider.

Organization orgs (type='organization'):
  departments = service_units, doctors = providers,
  each doctor can override timezone / slot duration / booking window,
  bookings carry unit_id + provider_id.
"""

from typing import Optional


def is_organization(org) -> bool:
    return getattr(org, "type", "individual") == "organization"


def effective_timezone(provider, org_timezone: str) -> str:
    tz = (getattr(provider, "timezone", None) or "").strip() if provider else ""
    return tz or org_timezone or "UTC"


def effective_slot_duration(provider, org_default: int) -> int:
    if provider is not None and getattr(provider, "slot_duration_minutes", None):
        return int(provider.slot_duration_minutes)
    return int(org_default)


def effective_booking_window(provider, org_default: int) -> int:
    if provider is not None and getattr(provider, "booking_window_days", None):
        return int(provider.booking_window_days)
    return int(org_default)


def validate_org_type(value: Optional[str]) -> str:
    if value == "organization":
        return "organization"
    return "individual"


async def get_managed_unit_ids(db, user_id) -> list:
    """Floors this staff member manages (empty = no dept restriction)."""
    from sqlalchemy.future import select

    from app.models.staff import StaffUnitScope

    rows = (
        await db.execute(
            select(StaffUnitScope.unit_id).where(
                StaffUnitScope.user_id == user_id
            )
        )
    ).all()
    return [str(r[0]) for r in rows]


async def get_provider_id_for_user(db, org_id, user_id):
    """Doctor row linked to this login (None = not a doctor login)."""
    from sqlalchemy.future import select

    from app.models.service_unit import Provider

    prov = (
        await db.execute(
            select(Provider).where(
                Provider.org_id == org_id, Provider.user_id == user_id
            )
        )
    ).scalars().first()
    return str(prov.id) if prov else None


async def require_organization_for(
    db=None,  # AsyncSession (passed explicitly to avoid Depends cycles)
    user=None,  # User (passed explicitly)
):
    """Enforce organization-only access. Raises 403 for individual orgs."""
    from fastapi import HTTPException
    from sqlalchemy.future import select

    from app.models.organization import Organization

    if db is None or user is None:
        raise HTTPException(status_code=500, detail="Server misconfigured")
    org = (
        await db.execute(select(Organization).where(Organization.id == user.org_id))
    ).scalars().first()
    if org is None or not is_organization(org):
        raise HTTPException(
            status_code=403,
            detail="Organization-only feature. Switch to an organization account to use departments and team management.",
        )
    return org
