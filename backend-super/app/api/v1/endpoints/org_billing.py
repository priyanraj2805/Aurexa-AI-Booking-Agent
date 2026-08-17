"""Org self-service billing (tenant-facing).

The tenant admin panel lets an org admin view its current plan and upgrade
or downgrade. The main (tenant) backend is authenticated for this on its
own side; it calls these endpoints with the shared service key.

Endpoints:
  - GET  /org-billing/plan?org_id=...  -> current plan + subscription + usage + catalog
  - POST /org-billing/plan             -> assign a new plan (creates/updates the subscription)
"""
import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.support import _require_service_key
from app.core.audit import write_audit_log
from app.core.billing import get_provider
from app.db.database import get_db

router = APIRouter(dependencies=[Depends(_require_service_key)])


class PlanChangeRequest(BaseModel):
    org_id: UUID
    plan_id: UUID
    requested_by_email: Optional[str] = None
    note: Optional[str] = None


async def _org_or_404(db: AsyncSession, org_id: UUID):
    row = (
        await db.execute(
            text("SELECT id, name FROM public.organizations WHERE id = :org_id"),
            {"org_id": org_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return row


async def _active_assignment(db: AsyncSession, org_id: UUID):
    row = (
        await db.execute(
            text(
                """
                SELECT a.id, a.assigned_at, a.note, a.expires_at,
                       p.id AS plan_id, p.code, p.name,
                       p.description, p.is_active, p.monthly_price_cents, p.period, p.limits
                FROM super_admin.plan_assignments a
                JOIN super_admin.plans p ON p.id = a.plan_id
                WHERE a.org_id = :org_id AND a.status = 'active'
                """
            ),
            {"org_id": org_id},
        )
    ).mappings().first()
    if not row:
        return None
    if row["expires_at"] is not None and row["expires_at"] < datetime.now(timezone.utc):
        # Sunset validity ended → the plan falls away.
        await db.execute(
            text(
                "UPDATE super_admin.plan_assignments "
                "SET status = 'revoked', revoked_at = now() "
                "WHERE id = :id AND status = 'active'"
            ),
            {"id": row["id"]},
        )
        await db.commit()
        return None
    return row


async def _org_usage(db: AsyncSession, org_id: UUID, month: str) -> dict:
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


@router.get("/plan")
async def get_org_plan(
    org_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Current plan, subscription status, monthly usage and the plan catalog."""
    org = await _org_or_404(db, org_id)
    assignment = await _active_assignment(db, org_id)

    subscription = (
        await db.execute(
            text(
                """
                SELECT s.id, s.plan_id, s.status, s.cancel_at_period_end,
                       s.current_period_start, s.current_period_end, s.created_at
                FROM super_admin.subscriptions s
                WHERE s.org_id = :org_id
                ORDER BY s.created_at DESC
                LIMIT 1
                """
            ),
            {"org_id": org_id},
        )
    ).mappings().first()

    catalog = (
        await db.execute(
            text(
                """
                SELECT id, code, name, description, is_active,
                       monthly_price_cents, period, limits
                FROM super_admin.plans
                WHERE is_active
                ORDER BY monthly_price_cents, code
                """
            )
        )
    ).mappings().all()

    month = datetime.now(timezone.utc).strftime("%Y-%m-01")

    result = {
        "org_id": str(org_id),
        "org_name": org["name"],
        "plan": dict(assignment) if assignment else None,
        "subscription": dict(subscription) if subscription else None,
        "usage_month": month,
        "usage": await _org_usage(db, org_id, month),
        "catalog": [
            {**dict(p), "limits": p["limits"] if isinstance(p["limits"], dict) else {}}
            for p in catalog
        ],
    }
    if assignment:
        result["plan"]["limits"] = (
            assignment["limits"] if isinstance(assignment["limits"], dict) else {}
        )
    return result


@router.post("/plan")
async def change_org_plan(
    payload: PlanChangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Assign a new plan; create or update the org's subscription row."""
    org = await _org_or_404(db, payload.org_id)

    plan = (
        await db.execute(
            text(
                "SELECT id, code, name, is_active FROM super_admin.plans WHERE id = :plan_id"
            ),
            {"plan_id": payload.plan_id},
        )
    ).mappings().first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not plan["is_active"]:
        raise HTTPException(status_code=422, detail="Cannot subscribe to an archived plan")

    old = await _active_assignment(db, payload.org_id)

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
                (:org_id, :plan_id, 'active', NULL, :email, :note)
            """
        ),
        {
            "org_id": payload.org_id,
            "plan_id": payload.plan_id,
            "email": payload.requested_by_email,
            "note": payload.note or "self-service plan change",
        },
    )

    existing_sub = (
        await db.execute(
            text(
                "SELECT id, status FROM super_admin.subscriptions "
                "WHERE org_id = :org_id AND status IN ('active', 'past_due', 'trialing')"
            ),
            {"org_id": payload.org_id},
        )
    ).mappings().first()
    if existing_sub:
        await db.execute(
            text(
                """
                UPDATE super_admin.subscriptions
                SET plan_id = :plan_id, status = 'active',
                    current_period_start = COALESCE(current_period_start, now()),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": existing_sub["id"], "plan_id": payload.plan_id},
        )
    else:
        provider = get_provider()
        ext = {}
        try:
            ext = await provider.create_subscription(str(payload.org_id), plan["code"])
        except Exception:
            # Provider failures must never block a plan change in the local DB.
            ext = {}
        await db.execute(
            text(
                """
                INSERT INTO super_admin.subscriptions
                    (org_id, plan_id, provider, provider_customer_id, provider_sub_id,
                     status, current_period_start, current_period_end)
                VALUES
                    (:org_id, :plan_id, :provider, :cust_id, :sub_id,
                     'active', now(), now() + interval '1 month')
                """
            ),
            {
                "org_id": payload.org_id,
                "plan_id": payload.plan_id,
                "provider": provider.name,
                "cust_id": ext.get("provider_customer_id"),
                "sub_id": ext.get("provider_sub_id"),
            },
        )
    await db.commit()

    await write_audit_log(
        actor_email=payload.requested_by_email,
        action="plan.assign",
        resource_type="organization",
        resource_id=str(payload.org_id),
        new_values={
            "org_name": org["name"],
            "from_plan": old["code"] if old else None,
            "to_plan": plan["code"],
            "source": "self_service",
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "org_id": str(payload.org_id), "plan_code": plan["code"]}