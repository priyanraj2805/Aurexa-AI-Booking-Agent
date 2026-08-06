"""Billing endpoints (Phase 5): subscriptions, invoices, refunds, reports.

Refund safeguards:
  - MFA step-up required for every refund (see /auth/mfa/stepup).
  - Refunds above REFUND_APPROVAL_THRESHOLD_CENTS also need a second admin:
    a billing.refund approval is filed (approvals table) and the approved
    request executes the refund via app.core.billing.execute_refund.
"""
import csv
import io
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.billing import execute_refund, get_provider
from app.core.config import settings
from app.core.rbac import CurrentAdmin, require_permission
from app.core.stepup import require_stepup
from app.db.database import get_db

router = APIRouter()

INVOICE_STATUSES = ("open", "paid", "refunded", "void", "uncollectible")


class SubscriptionCreate(BaseModel):
    org_id: UUID
    plan_id: UUID


class InvoiceCreate(BaseModel):
    amount_cents: int
    description: Optional[str] = None


class RefundRequest(BaseModel):
    reason: str


async def _get_invoice_or_404(db: AsyncSession, invoice_id: UUID):
    row = (
        await db.execute(
            text(
                """
                SELECT id, org_id, provider, provider_invoice_id, number,
                       amount_cents, currency, status, invoice_url, description,
                       due_at, paid_at, refunded_at, refund_amount_cents, created_at
                FROM super_admin.invoices WHERE id = :invoice_id
                """
            ),
            {"invoice_id": invoice_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return row


@router.get("/subscriptions")
async def list_subscriptions(
    org_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("billing.view")),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT s.id, s.org_id, o.name AS org_name, s.plan_id, p.code AS plan_code,
                       p.name AS plan_name, s.provider, s.status,
                       s.current_period_start, s.current_period_end,
                       s.cancel_at_period_end, s.canceled_at, s.created_at
                FROM super_admin.subscriptions s
                JOIN super_admin.plans p ON p.id = s.plan_id
                JOIN public.organizations o ON o.id = s.org_id
                WHERE (CAST(:org_id AS uuid) IS NULL OR s.org_id = :org_id)
                ORDER BY s.created_at DESC
                """
            ),
            {"org_id": org_id},
        )
    ).mappings()
    return {"items": [dict(r) for r in rows]}


@router.post("/subscriptions")
async def create_subscription(
    payload: SubscriptionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("billing.manage")),
):
    org = (
        await db.execute(
            text("SELECT id, name FROM public.organizations WHERE id = :org_id"),
            {"org_id": payload.org_id},
        )
    ).mappings().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
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

    existing = (
        await db.execute(
            text(
                "SELECT id, status FROM super_admin.subscriptions "
                "WHERE org_id = :org_id AND status IN ('active', 'past_due', 'trialing')"
            ),
            {"org_id": payload.org_id},
        )
    ).mappings().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Organization already has a {existing['status']} subscription",
        )

    provider = get_provider()
    ext = await provider.create_subscription(str(payload.org_id), plan["code"])
    sub_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.subscriptions
                    (org_id, plan_id, provider, provider_customer_id, provider_sub_id, status)
                VALUES
                    (:org_id, :plan_id, :provider, :cust_id, :sub_id, 'active')
                RETURNING id
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
    ).scalar()
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="billing.subscription.create",
        resource_type="organization",
        resource_id=str(payload.org_id),
        new_values={"org_name": org["name"], "plan_code": plan["code"], "provider": provider.name},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "subscription_id": str(sub_id), "provider": provider.name}


@router.post("/subscriptions/{subscription_id}/cancel")
async def cancel_subscription(
    subscription_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("billing.manage")),
):
    """Cancel an active subscription immediately (at the provider + in DB)."""
    row = (
        await db.execute(
            text(
                """
                SELECT s.id, s.org_id, o.name AS org_name, s.plan_id, p.code AS plan_code,
                       s.provider, s.status, s.provider_sub_id
                FROM super_admin.subscriptions s
                JOIN super_admin.plans p ON p.id = s.plan_id
                JOIN public.organizations o ON o.id = s.org_id
                WHERE s.id = :subscription_id
                """
            ),
            {"subscription_id": subscription_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if row["status"] != "active":
        raise HTTPException(
            status_code=422, detail=f"Cannot cancel a {row['status']} subscription"
        )

    provider = get_provider()
    try:
        await provider.cancel_subscription(row["provider_sub_id"] or "")
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Provider cancel failed: {exc}"
        )

    await db.execute(
        text(
            """
            UPDATE super_admin.subscriptions
            SET status = 'canceled', canceled_at = now(), cancel_at_period_end = FALSE
            WHERE id = :subscription_id
            """
        ),
        {"subscription_id": subscription_id},
    )
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="billing.subscription.cancel",
        resource_type="organization",
        resource_id=str(row["org_id"]),
        new_values={"org_name": row["org_name"], "plan_code": row["plan_code"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "subscription_id": str(subscription_id)}


@router.get("/invoices")
async def list_invoices(
    org_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("billing.view")),
):
    where = []
    params: dict = {"limit": limit}
    if org_id:
        where.append("i.org_id = :org_id")
        params["org_id"] = org_id
    if status:
        if status.lower() not in INVOICE_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {INVOICE_STATUSES}")
        where.append("i.status = :status")
        params["status"] = status.lower()
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT i.id, i.org_id, o.name AS org_name, i.number, i.amount_cents,
                       i.currency, i.status, i.invoice_url, i.description,
                       i.due_at, i.paid_at, i.refunded_at, i.refund_amount_cents, i.created_at
                FROM super_admin.invoices i
                JOIN public.organizations o ON o.id = i.org_id
                {where_sql}
                ORDER BY i.created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings()
    return {"items": [dict(r) for r in rows]}


@router.post("/invoices")
async def issue_invoice(
    org_id: UUID,
    payload: InvoiceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("billing.manage")),
):
    if payload.amount_cents <= 0:
        raise HTTPException(status_code=422, detail="amount_cents must be positive")
    org = (
        await db.execute(
            text("SELECT id, name FROM public.organizations WHERE id = :org_id"),
            {"org_id": org_id},
        )
    ).mappings().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    number = f"INV-{datetime.now(timezone.utc):%Y%m}-{uuid_mod.uuid4().hex[:6].upper()}"
    inv_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.invoices
                    (org_id, provider, provider_invoice_id, number, amount_cents, status, description)
                VALUES
                    (:org_id, :provider, :provider_invoice_id, :number, :amount, 'open', :desc)
                RETURNING id
                """
            ),
            {
                "org_id": org_id,
                "provider": "mock",
                "provider_invoice_id": f"in_mock_{uuid_mod.uuid4().hex[:16]}",
                "number": number,
                "amount": payload.amount_cents,
                "desc": payload.description,
            },
        )
    ).scalar()
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="billing.invoice.issue",
        resource_type="organization",
        resource_id=str(org_id),
        new_values={"org_name": org["name"], "number": number, "amount_cents": payload.amount_cents},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "invoice_id": str(inv_id), "number": number}


@router.post("/invoices/{invoice_id}/retry")
async def retry_invoice(
    invoice_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("billing.retry_payment")),
):
    row = await _get_invoice_or_404(db, invoice_id)
    if row["status"] != "open":
        raise HTTPException(status_code=409, detail=f"Invoice is {row['status']}")

    provider = get_provider()
    try:
        result = await provider.retry_invoice(row["provider_invoice_id"])
    except Exception:
        raise HTTPException(status_code=502, detail="Payment provider error")

    if not result.get("paid"):
        raise HTTPException(status_code=502, detail="Payment failed")
    await db.execute(
        text(
            "UPDATE super_admin.invoices SET status = 'paid', paid_at = now() WHERE id = :invoice_id"
        ),
        {"invoice_id": invoice_id},
    )
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="billing.invoice.retry",
        resource_type="invoice",
        resource_id=str(invoice_id),
        new_values={"number": row["number"], "status": "paid"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "status": "paid"}


@router.post("/invoices/{invoice_id}/refund")
async def refund_invoice(
    invoice_id: UUID,
    payload: RefundRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("billing.refund")),
):
    require_stepup(request, admin)
    row = await _get_invoice_or_404(db, invoice_id)
    if row["status"] != "paid":
        raise HTTPException(status_code=409, detail=f"Invoice is {row['status']}")

    amount = row["amount_cents"]
    if amount > settings.REFUND_APPROVAL_THRESHOLD_CENTS:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        approval_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO super_admin.approvals
                        (request_type, resource_type, resource_id, payload, status,
                         requested_by_id, requested_by_email, expires_at)
                    VALUES
                        ('billing.refund', 'invoice', :invoice_id, :payload, 'PENDING',
                         :admin_id, :admin_email, :expires_at)
                    RETURNING id
                    """
                ),
                {
                    "invoice_id": str(invoice_id),
                    "payload": __import__("json").dumps(
                        {
                            "invoice_id": str(invoice_id),
                            "amount_cents": amount,
                            "reason": payload.reason,
                        }
                    ),
                    "admin_id": admin.id,
                    "admin_email": admin.email,
                    "expires_at": expires_at,
                },
            )
        ).scalar()
        await db.commit()

        await write_audit_log(
            actor_id=admin.id,
            actor_email=admin.email,
            action="billing.refund.request",
            resource_type="approval",
            resource_id=str(approval_id),
            new_values={
                "invoice_id": str(invoice_id),
                "amount_cents": amount,
                "reason": payload.reason,
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return {
            "ok": True,
            "status": "PENDING_APPROVAL",
            "approval_id": str(approval_id),
            "detail": (
                f"Refund of ${amount / 100:.2f} exceeds the approval threshold; "
                "a second admin must approve it"
            ),
        }

    executed = await execute_refund(
        db, invoice_id, amount, reason=payload.reason[:200]
    )
    if not executed:
        raise HTTPException(status_code=409, detail="Refund could not be executed")
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="billing.refund.executed",
        resource_type="invoice",
        resource_id=str(invoice_id),
        new_values={"number": row["number"], "amount_cents": amount, "reason": payload.reason},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "status": "refunded"}


@router.get("/reports/revenue")
async def revenue_report(
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("billing.view")),
):
    active = (
        await db.execute(
            text(
                """
                SELECT COALESCE(SUM(p.monthly_price_cents), 0) AS mrr
                FROM super_admin.subscriptions s
                JOIN super_admin.plans p ON p.id = s.plan_id
                WHERE s.status IN ('active', 'past_due', 'trialing')
                """
            )
        )
    ).scalar()
    counts = (
        await db.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE status IN ('active', 'past_due', 'trialing')) AS active_subs,
                    count(*) FILTER (WHERE status = 'canceled' AND canceled_at >= now() - interval '30 days') AS canceled_30d
                FROM super_admin.subscriptions
                """
            )
        )
    ).mappings().first()
    aging_rows = (
        await db.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE due_at IS NULL OR due_at >= now()) AS bucket_0_30,
                    count(*) FILTER (WHERE due_at < now() - interval '0 days' AND due_at >= now() - interval '30 days') AS bucket_31_60,
                    count(*) FILTER (WHERE due_at < now() - interval '30 days' AND due_at >= now() - interval '60 days') AS bucket_61_90,
                    count(*) FILTER (WHERE due_at < now() - interval '60 days') AS bucket_90_plus,
                    COALESCE(SUM(amount_cents) FILTER (WHERE status = 'open'), 0) AS open_total
                FROM super_admin.invoices
                WHERE status = 'open'
                """
            )
        )
    ).mappings().first()

    active_subs = int(counts["active_subs"])
    canceled_30d = int(counts["canceled_30d"])
    churn_pct = round(canceled_30d / (active_subs + canceled_30d) * 100, 2) if (active_subs + canceled_30d) else 0.0

    return {
        "mrr_cents": int(active),
        "arr_cents": int(active) * 12,
        "active_subscriptions": active_subs,
        "canceled_30d": canceled_30d,
        "churn_rate_pct": churn_pct,
        "invoice_aging": {
            "0_30_days": int(aging_rows["bucket_0_30"]),
            "31_60_days": int(aging_rows["bucket_31_60"]),
            "61_90_days": int(aging_rows["bucket_61_90"]),
            "90_plus_days": int(aging_rows["bucket_90_plus"]),
            "open_total_cents": int(aging_rows["open_total"]),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/reports/revenue.csv")
async def revenue_report_csv(
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("billing.view")),
):
    data = await revenue_report(db, _)
    aging = data["invoice_aging"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "value"])
    for key, value in (
        ("mrr_cents", data["mrr_cents"]),
        ("arr_cents", data["arr_cents"]),
        ("active_subscriptions", data["active_subscriptions"]),
        ("canceled_30d", data["canceled_30d"]),
        ("churn_rate_pct", data["churn_rate_pct"]),
        ("aging_0_30_days", aging["0_30_days"]),
        ("aging_31_60_days", aging["31_60_days"]),
        ("aging_61_90_days", aging["61_90_days"]),
        ("aging_90_plus_days", aging["90_plus_days"]),
        ("aging_open_total_cents", aging["open_total_cents"]),
    ):
        writer.writerow([key, value])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="revenue_report.csv"'},
    )
