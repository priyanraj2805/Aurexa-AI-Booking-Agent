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


async def resolve_booking_charge(
    db: AsyncSession,
    org_id: uuid.UUID,
    cfg,
    unit_id: Any = None,
    provider_id: Any = None,
) -> Tuple[int, str]:
    """Amount to actually charge for a booking.

    Priority: catalog fee for the selected department/member first
    (unit+provider > provider-only > unit-only), flat org price as
    fallback. The tenant sets per-department/member fees in the catalog;
    the flat price is only a legacy fallback.
    """
    currency = (
        getattr(cfg, "appointment_currency", "INR") or "INR"
    ).upper()
    try:
        fee_paise, fee_cur = await resolve_effective_fee(
            db, org_id, unit_id, provider_id
        )
    except Exception:
        fee_paise, fee_cur = None, None
    if fee_paise:
        return int(fee_paise), (fee_cur or currency).upper()
    flat = int(getattr(cfg, "appointment_price_paise", 0) or 0)
    return flat, currency


async def providers_for_date(
    db: AsyncSession,
    org_id,
    unit_id,
    target_date,
) -> List[Dict[str, Any]]:
    """Active dept members working on a specific date (override → template).

    A member works that day when their day row (own override else org
    template) is active and no CLOSED exception (own or org-wide) covers
    the date. CUSTOM_HOURS counts as working. Each entry carries the
    effective fee so pickers can show it. Empty unit → [].
    """
    import uuid as _uuid
    from datetime import date as _date

    from app.models.booking import ExceptionType, ScheduleDay, ScheduleException

    try:
        ouid = unit_id if isinstance(unit_id, _uuid.UUID) else _uuid.UUID(str(unit_id))
    except Exception:
        return []
    if isinstance(target_date, str):
        try:
            target_date = _date.fromisoformat(target_date)
        except Exception:
            return []

    links = (
        await db.execute(select(UnitProvider).where(UnitProvider.unit_id == ouid))
    ).scalars().all()
    if not links:
        return []

    try:
        ouuid = org_id if isinstance(org_id, _uuid.UUID) else _uuid.UUID(str(org_id))
    except Exception:
        return []
    weekday = target_date.weekday()
    out: List[Dict[str, Any]] = []
    for a in links:
        p = (
            await db.execute(
                select(Provider).where(
                    Provider.id == a.provider_id,
                    Provider.org_id == ouuid,
                    Provider.is_active.is_(True),
                )
            )
        ).scalars().first()
        if p is None:
            continue
        # Day row: own override wins, else org template.
        day = (
            await db.execute(
                select(ScheduleDay).where(
                    ScheduleDay.org_id == ouuid,
                    ScheduleDay.day_of_week == weekday,
                    ScheduleDay.provider_id == p.id,
                )
            )
        ).scalars().first()
        if day is None:
            tmpl = (
                await db.execute(
                    select(ScheduleDay).where(
                        ScheduleDay.org_id == ouuid,
                        ScheduleDay.day_of_week == weekday,
                        ScheduleDay.provider_id.is_(None),
                    )
                )
            ).scalars().first()
            day = tmpl
        if day is None or not bool(getattr(day, "is_active", False)):
            continue
        # CLOSED exception (own or org-wide) knocks the member out that day.
        excs = (
            await db.execute(
                select(ScheduleException).where(
                    ScheduleException.org_id == ouuid,
                    ScheduleException.exception_date == target_date,
                )
            )
        ).scalars().all()
        knocked = False
        for e in excs:
            epid = getattr(e, "provider_id", None)
            if epid is not None and epid != p.id:
                continue
            if str(getattr(e, "exception_type", "")) == ExceptionType.CLOSED.value:
                knocked = True
                break
        if knocked:
            continue
        fee_paise, fee_cur = await resolve_effective_fee(db, ouuid, ouid, p.id)
        fee_display = None
        if fee_paise:
            if (fee_cur or "INR").upper() == "INR":
                fee_display = f"₹{int(fee_paise) / 100:g}"
            else:
                fee_display = f"{fee_cur} {int(fee_paise) / 100:,.2f}"
        out.append(
            {
                "id": str(p.id),
                "full_name": p.full_name,
                "role_label": getattr(p, "role_label", None),
                "fee_display": fee_display,
            }
        )
    out.sort(key=lambda x: x["full_name"].lower())
    return out


def match_provider_selection(offered: List[Dict[str, Any]], text: str) -> Optional[Dict[str, Any]]:
    """Match a typed/tapped reply to an offered team member.

    Accepts: exact id, 1-based position ("2"), exact name, or a unique
    name fragment ("sharma"). Returns the matched entry or None.
    """
    t = str(text or "").strip()
    if not t or not offered:
        return None
    low = t.lower()
    for o in offered:
        if low == str(o.get("id") or "").lower():
            return o
    if low.isdigit():
        i = int(low) - 1
        if 0 <= i < len(offered):
            return offered[i]
    exact = [o for o in offered if str(o.get("full_name") or "").strip().lower() == low]
    if len(exact) == 1:
        return exact[0]
    frag = [o for o in offered if low in str(o.get("full_name") or "").lower()]
    if len(frag) == 1:
        return frag[0]
    return None


async def resolve_pending_provider(
    db: AsyncSession,
    org_id,
    session,
    message_text: str,
) -> Optional[Dict[str, Any]]:
    """Pin a team member chosen at the post-date provider step.

    Runs at engine entry (both runtimes, all channels): when the session
    carries a department, no member yet, and a pending offered list, a
    tap/number/name reply ("2", "Dr Sharma", provider id) pins the member
    onto the session. Returns the matched entry or None (caller proceeds
    normally). Sessions without a pending offer are untouched.
    """
    try:
        cd = getattr(session, "collected_data", None) or {}
        if not isinstance(cd, dict):
            return None
        offered = cd.get("_provider_options") or []
        if not offered:
            return None
        unit_id = getattr(session, "unit_id", None) or cd.get("unit_id")
        prov_now = getattr(session, "provider_id", None) or cd.get("provider_id")
        if not unit_id or prov_now:
            return None
        # Rebuild full entries (stored offer may be ids only).
        ids = [
            str(o.get("id") if isinstance(o, dict) else o)
            for o in offered
            if (o.get("id") if isinstance(o, dict) else o)
        ]
        entries: List[Dict[str, Any]] = []
        if ids and all(isinstance(o, dict) and o.get("full_name") for o in offered):
            entries = [o for o in offered if isinstance(o, dict)]
        else:
            from app.models.service_unit import Provider as _P

            for pid in ids:
                try:
                    import uuid as _uuid

                    p = (
                        await db.execute(
                            select(_P).where(
                                _P.id == _uuid.UUID(str(pid)),
                                _P.org_id == org_id,
                            )
                        )
                    ).scalars().first()
                    if p is not None and bool(getattr(p, "is_active", True)):
                        entries.append({"id": str(p.id), "full_name": p.full_name})
                except Exception:
                    continue
        match = match_provider_selection(entries, message_text)
        if match is None:
            return None
        unit, provider = await validate_catalog_selection(
            db, org_id, unit_id, match.get("id")
        )
        if provider is None:
            return None
        try:
            session.unit_id = unit.id if unit is not None else session.unit_id
            session.provider_id = provider.id
        except Exception:
            pass
        cd = dict(cd)
        cd["unit_id"] = str(unit.id) if unit is not None else cd.get("unit_id")
        cd["unit_name"] = unit.name if unit is not None else cd.get("unit_name")
        cd["provider_id"] = str(provider.id)
        cd["provider_name"] = provider.full_name
        cd.pop("_provider_options", None)
        try:
            session.collected_data = cd
        except Exception:
            pass
        return {"id": str(provider.id), "full_name": provider.full_name}
    except Exception:
        return None


async def booking_email_context(db: AsyncSession, booking) -> Dict[str, Any]:
    """Department/member/fee lines for confirmation emails (best-effort).

    Reads the persisted booking row so emails always match what was stored,
    even when the chat session carried no catalog context (individual flow
    returns {} and emails render exactly as before).
    """
    out: Dict[str, Any] = {}
    try:
        unit_id = getattr(booking, "unit_id", None)
        provider_id = getattr(booking, "provider_id", None)
        if unit_id:
            u = (
                await db.execute(
                    select(ServiceUnit).where(ServiceUnit.id == unit_id)
                )
            ).scalars().first()
            if u is not None:
                out["unit_name"] = u.name
        if provider_id:
            p = (
                await db.execute(
                    select(Provider).where(Provider.id == provider_id)
                )
            ).scalars().first()
            if p is not None:
                out["provider_name"] = p.full_name
                if getattr(p, "role_label", None):
                    out["provider_role"] = p.role_label
        paise = getattr(booking, "amount_paise", None)
        if paise:
            cur = (getattr(booking, "currency", None) or "INR").upper()
            if cur == "INR":
                out["fee_display"] = f"₹{int(paise) / 100:g}"
            else:
                out["fee_display"] = f"{cur} {int(paise) / 100:,.2f}"
    except Exception:
        pass
    return out


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
                fee_paise, fee_cur = await resolve_effective_fee(db, org_id, u.id, p.id)
                fee_display = None
                if fee_paise:
                    if (fee_cur or "INR").upper() == "INR":
                        fee_display = f"₹{fee_paise / 100:g}"
                    else:
                        fee_display = f"{fee_cur} {fee_paise / 100:,.2f}"
                provs.append(
                    {
                        "id": str(p.id),
                        "full_name": p.full_name,
                        "role_label": p.role_label,
                        "fee_display": fee_display,
                    }
                )
        provs.sort(key=lambda x: x["full_name"].lower())
        out.append(
            {
                "id": str(u.id),
                "name": u.name,
                "description": u.description,
                "providers": provs,
            }
        )
    return {"units": out, "has_catalog": bool(out)}
