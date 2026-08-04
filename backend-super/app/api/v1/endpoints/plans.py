"""Plans & usage limits (Phase 5).

Plan catalog CRUD + per-org plan assignments + monthly usage read-out.

  - plans are soft-archived when still assigned (is_active = FALSE);
    unused plans can be hard-deleted.
  - one ACTIVE assignment per org (partial unique index); changing an org's
    plan revokes the current row (history kept) and inserts a new one.
  - usage comes from super_admin.usage_events (monthly partitions, written
    by the main backend telemetry when TELEMETRY_ENABLED).
  - limits keys are whitelisted; a NULL value means unlimited.
"""
import json
import re
from datetime import datetime, timezone
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()

LIMIT_KEYS = ("bookings_per_month", "ai_tokens_per_month", "seats", "storage_gb")
PERIODS = ("monthly", "annual")
CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,49}$")


class PlanCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    monthly_price_cents: int = 0
    period: str = "monthly"
    limits: Dict[str, Optional[int]] = {}


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    monthly_price_cents: Optional[int] = None
    period: Optional[str] = None
    limits: Optional[Dict[str, Optional[int]]] = None
    is_active: Optional[bool] = None


class PlanAssign(BaseModel):
    org_id: UUID
    note: Optional[str] = None


def _validate_limits(limits: Dict[str, Optional[int]]) -> None:
    for key, value in limits.items():
        if key not in LIMIT_KEYS:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown limit key: {key} (allowed: {', '.join(LIMIT_KEYS)})",
            )
        if value is not None and (not isinstance(value, int) or value < 0):
            raise HTTPException(
                status_code=422, detail=f"Limit {key} must be a non-negative integer or null"
            )


def _plan_row(row) -> dict:
    d = dict(row)
    d["limits"] = row["limits"] if isinstance(row["limits"], dict) else {}
    return d


async def _get_plan_or_404(db: AsyncSession, plan_id: UUID):
    row = (
        await db.execute(
            text(
                """
                SELECT id, code, name, description, is_active,
                       monthly_price_cents, period, limits, created_at, updated_at
                FROM super_admin.plans WHERE id = :plan_id
                """
            ),
            {"plan_id": plan_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    return row


@router.get("")
async def list_plans(
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("plans.view")),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, code, name, description, is_active,
                       monthly_price_cents, period, limits, created_at, updated_at
                FROM super_admin.plans
                WHERE (:include_inactive OR is_active)
                ORDER BY monthly_price_cents, code
                """
            ),
            {"include_inactive": include_inactive},
        )
    ).mappings()
    return {"items": [_plan_row(r) for r in rows]}


@router.get("/{plan_id}")
async def get_plan(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("plans.view")),
):
    return _plan_row(await _get_plan_or_404(db, plan_id))


@router.post("")
async def create_plan(
    payload: PlanCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("plans.manage")),
):
    if not CODE_RE.match(payload.code):
        raise HTTPException(
            status_code=422,
            detail="code must be 2-50 chars: lowercase letters, digits, underscore, hyphen",
        )
    if payload.period not in PERIODS:
        raise HTTPException(status_code=422, detail=f"period must be one of {PERIODS}")
    if payload.monthly_price_cents < 0:
        raise HTTPException(status_code=422, detail="monthly_price_cents cannot be negative")
    _validate_limits(payload.limits)

    try:
        plan_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO super_admin.plans
                        (code, name, description, is_active, monthly_price_cents, period, limits)
                    VALUES
                        (:code, :name, :desc, TRUE, :price, :period, :limits)
                    RETURNING id
                    """
                ),
                {
                    "code": payload.code,
                    "name": payload.name,
                    "desc": payload.description,
                    "price": payload.monthly_price_cents,
                    "period": payload.period,
                    "limits": json.dumps(payload.limits),
                },
            )
        ).scalar()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Plan code already exists: {payload.code}")

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="plan.create",
        resource_type="plan",
        resource_id=str(plan_id),
        new_values=payload.model_dump(exclude_none=True),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "plan_id": str(plan_id)}


@router.patch("/{plan_id}")
async def update_plan(
    plan_id: UUID,
    payload: PlanUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("plans.manage")),
):
    await _get_plan_or_404(db, plan_id)

    if payload.period is not None and payload.period not in PERIODS:
        raise HTTPException(status_code=422, detail=f"period must be one of {PERIODS}")
    if payload.monthly_price_cents is not None and payload.monthly_price_cents < 0:
        raise HTTPException(status_code=422, detail="monthly_price_cents cannot be negative")
    if payload.limits is not None:
        _validate_limits(payload.limits)

    updates = []
    params: dict = {"plan_id": plan_id}
    for col, val in (
        ("name", payload.name),
        ("description", payload.description),
        ("monthly_price_cents", payload.monthly_price_cents),
        ("period", payload.period),
        ("is_active", payload.is_active),
    ):
        if val is not None:
            updates.append(f"{col} = :{col}")
            params[col] = val
    if payload.limits is not None:
        updates.append("limits = :limits")
        params["limits"] = json.dumps(payload.limits)
    updates.append("updated_at = now()")

    await db.execute(
        text(
            f"UPDATE super_admin.plans SET {', '.join(updates)} "
            "WHERE id = :plan_id RETURNING id"
        ),
        params,
    )
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="plan.update",
        resource_type="plan",
        resource_id=str(plan_id),
        new_values=payload.model_dump(exclude_none=True),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.delete("/{plan_id}")
async def archive_plan(
    plan_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("plans.manage")),
):
    row = await _get_plan_or_404(db, plan_id)
    active_assignments = (
        await db.execute(
            text(
                "SELECT count(*) AS n FROM super_admin.plan_assignments "
                "WHERE plan_id = :plan_id AND status = 'active'"
            ),
            {"plan_id": plan_id},
        )
    ).scalar()

    if active_assignments:
        await db.execute(
            text("UPDATE super_admin.plans SET is_active = FALSE, updated_at = now() WHERE id = :plan_id"),
            {"plan_id": plan_id},
        )
        action = "plan.archive"
    else:
        await db.execute(
            text("DELETE FROM super_admin.plans WHERE id = :plan_id"),
            {"plan_id": plan_id},
        )
        action = "plan.delete"
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action=action,
        resource_type="plan",
        resource_id=str(plan_id),
        new_values={"code": row["code"], "archived": active_assignments > 0},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "archived": bool(active_assignments)}


@router.post("/{plan_id}/assign")
async def assign_plan(
    plan_id: UUID,
    payload: PlanAssign,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("plans.assign")),
):
    plan = await _get_plan_or_404(db, plan_id)
    if not plan["is_active"]:
        raise HTTPException(status_code=422, detail="Cannot assign an archived plan")

    org = (
        await db.execute(
            text("SELECT id, name FROM public.organizations WHERE id = :org_id"),
            {"org_id": payload.org_id},
        )
    ).mappings().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    old = (
        await db.execute(
            text(
                """
                SELECT a.plan_id, p.code
                FROM super_admin.plan_assignments a
                JOIN super_admin.plans p ON p.id = a.plan_id
                WHERE a.org_id = :org_id AND a.status = 'active'
                """
            ),
            {"org_id": payload.org_id},
        )
    ).mappings().first()

    await db.execute(
        text(
            "UPDATE super_admin.plan_assignments SET status = 'revoked', revoked_at = now() "
            "WHERE org_id = :org_id AND status = 'active'"
        ),
        {"org_id": payload.org_id},
    )
    await db.execute(
        text(
            """
            INSERT INTO super_admin.plan_assignments
                (org_id, plan_id, status, assigned_by_id, assigned_by_email, note)
            VALUES
                (:org_id, :plan_id, 'active', :admin_id, :admin_email, :note)
            """
        ),
        {
            "org_id": payload.org_id,
            "plan_id": plan_id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "note": payload.note,
        },
    )
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="plan.assign",
        resource_type="organization",
        resource_id=str(payload.org_id),
        new_values={
            "org_name": org["name"],
            "from_plan": old["code"] if old else None,
            "to_plan": plan["code"],
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "org_id": str(payload.org_id), "plan_code": plan["code"]}


async def _org_usage(db: AsyncSession, org_id: UUID, month: str) -> dict:
    """Monthly usage totals from usage_events, keyed by metric."""
    rows = (
        await db.execute(
            text(
                """
                SELECT metric, count FROM super_admin.usage_events
                WHERE org_id = :org_id AND event_month = :month
                """
            ),
            {"org_id": org_id, "month": datetime.strptime(month, "%Y-%m-%d").date()},
        )
    ).mappings()
    return {r["metric"]: r["count"] for r in rows}


@router.get("/orgs/{org_id}/plan")
async def get_org_plan(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("plans.view")),
):
    org = (
        await db.execute(
            text("SELECT id, name FROM public.organizations WHERE id = :org_id"),
            {"org_id": org_id},
        )
    ).mappings().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    row = (
        await db.execute(
            text(
                """
                SELECT a.assigned_at, a.note, p.id AS plan_id, p.code, p.name,
                       p.description, p.is_active, p.monthly_price_cents, p.period, p.limits
                FROM super_admin.plan_assignments a
                JOIN super_admin.plans p ON p.id = a.plan_id
                WHERE a.org_id = :org_id AND a.status = 'active'
                """
            ),
            {"org_id": org_id},
        )
    ).mappings().first()

    month = datetime.now(timezone.utc).strftime("%Y-%m-01")
    usage = await _org_usage(db, org_id, month)

    result = {
        "org_id": str(org_id),
        "org_name": org["name"],
        "plan": dict(row) if row else None,
        "usage_month": month,
        "usage": usage,
    }
    if row:
        result["plan"]["limits"] = row["limits"] if isinstance(row["limits"], dict) else {}
    return result


@router.get("/orgs/{org_id}/usage")
async def get_org_usage(
    org_id: UUID,
    month: str = Query(None, description="YYYY-MM; defaults to current month"),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("plans.view")),
):
    org = (
        await db.execute(
            text("SELECT id FROM public.organizations WHERE id = :org_id"),
            {"org_id": org_id},
        )
    ).mappings().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    usage = await _org_usage(db, org_id, f"{month}-01")
    return {"org_id": str(org_id), "month": month, "metrics": usage}
