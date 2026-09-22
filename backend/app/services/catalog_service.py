"""Service catalog helpers shared by booking paths and the widget.

- validate_catalog_selection: org-scoped validation of unit/provider IDs.
  Raises ValueError on unknown, inactive, cross-org, or unassigned combos.
- resolve_effective_fee: best active fee match —
  (unit + provider) > provider-default > unit-default, latest wins ties.
- get_catalog_snapshot: active units with their active providers for
  widget config / agent selection UI.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.service_unit import Provider, ProviderFee, ServiceUnit, UnitProvider

logger = logging.getLogger(__name__)


def _parse_uuid(raw: Any) -> Optional[uuid.UUID]:
    if not raw:
        return None
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError, TypeError):
        return None


async def validate_catalog_selection(
    db: AsyncSession,
    org_id: uuid.UUID,
    unit_id: Any,
    provider_id: Any,
) -> Tuple[Optional[ServiceUnit], Optional[Provider]]:
    """Returns (unit, provider); either may be None when not selected."""
    unit = None
    provider = None
    uid = _parse_uuid(unit_id)
    pid = _parse_uuid(provider_id)
    if unit_id and uid is None:
        raise ValueError("Selected unit is invalid.")
    if provider_id and pid is None:
        raise ValueError("Selected provider is invalid.")
    if uid is not None:
        unit = (
            await db.execute(
                select(ServiceUnit).where(
                    ServiceUnit.id == uid, ServiceUnit.org_id == org_id
                )
            )
        ).scalars().first()
        if unit is None or not unit.is_active:
            raise ValueError("Selected unit is no longer available.")
    if pid is not None:
        provider = (
            await db.execute(
                select(Provider).where(
                    Provider.id == pid, Provider.org_id == org_id
                )
            )
        ).scalars().first()
        if provider is None or not provider.is_active:
            raise ValueError("Selected provider is no longer available.")
    if unit is not None and provider is not None:
        link = (
            await db.execute(
                select(UnitProvider).where(
                    UnitProvider.unit_id == unit.id,
                    UnitProvider.provider_id == provider.id,
                )
            )
        ).scalars().first()
        if link is None:
            raise ValueError("Selected provider is not part of the selected unit.")
    return unit, provider


async def resolve_effective_fee(
    db: AsyncSession,
    org_id: uuid.UUID,
    unit_id: Any,
    provider_id: Any,
) -> Tuple[Optional[int], Optional[str]]:
    """Best active fee: (unit+provider) > provider-only > unit-only."""
    uid = _parse_uuid(unit_id)
    pid = _parse_uuid(provider_id)
    if uid is None and pid is None:
        return None, None
    candidates = []
    if uid is not None and pid is not None:
        candidates.append(
            (await db.execute(
                select(ProviderFee)
                .where(
                    ProviderFee.org_id == org_id,
                    ProviderFee.is_active.is_(True),
                    ProviderFee.unit_id == uid,
                    ProviderFee.provider_id == pid,
                )
                .order_by(ProviderFee.created_at.desc())
            )).scalars().first()
        )
    if pid is not None:
        candidates.append(
            (await db.execute(
                select(ProviderFee)
                .where(
                    ProviderFee.org_id == org_id,
                    ProviderFee.is_active.is_(True),
                    ProviderFee.provider_id == pid,
                    ProviderFee.unit_id.is_(None),
                )
                .order_by(ProviderFee.created_at.desc())
            )).scalars().first()
        )
    if uid is not None:
        candidates.append(
            (await db.execute(
                select(ProviderFee)
                .where(
                    ProviderFee.org_id == org_id,
                    ProviderFee.is_active.is_(True),
                    ProviderFee.unit_id == uid,
                    ProviderFee.provider_id.is_(None),
                )
                .order_by(ProviderFee.created_at.desc())
            )).scalars().first()
        )
    for fee in candidates:
        if fee is not None and fee.amount_paise:
            return int(fee.amount_paise), fee.currency or "INR"
    return None, None


async def get_catalog_snapshot(db: AsyncSession, org_id: uuid.UUID) -> Dict[str, Any]:
    """Active units each with their active assigned providers."""
    units = (
        await db.execute(
            select(ServiceUnit)
            .where(ServiceUnit.org_id == org_id, ServiceUnit.is_active.is_(True))
            .order_by(ServiceUnit.name)
        )
    ).scalars().all()
    out: List[Dict[str, Any]] = []
    for u in units:
        links = (
            await db.execute(
                select(UnitProvider).where(UnitProvider.unit_id == u.id)
            )
        ).scalars().all()
        provs = []
        for a in links:
            p = (
                await db.execute(
                    select(Provider).where(
                        Provider.id == a.provider_id,
                        Provider.is_active.is_(True),
                    )
                )
            ).scalars().first()
            if p is not None:
                provs.append(
                    {
                        "id": str(p.id),
                        "full_name": p.full_name,
                        "role_label": p.role_label,
                    }
                )
        provs.sort(key=lambda x: x["full_name"].lower())
        out.append({"id": str(u.id), "name": u.name, "providers": provs})
    return {"units": out, "has_catalog": bool(out)}
