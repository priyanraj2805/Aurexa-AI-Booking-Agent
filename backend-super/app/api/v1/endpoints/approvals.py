"""Two-person approval workflow (Phase 4).

Destructive actions (org delete) are not executed directly: an admin files a
request, and a SECOND admin approves it. Rules:

  - 24h expiry: approving/rejecting an expired request fails (status EXPIRED).
  - Second person: the reviewer must be a different admin than the requester.
  - Approval executes the bridge call; status → EXECUTED on success, stays
    APPROVED on bridge failure so it can be retried.
  - Every transition is written to the hash-chained audit log.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.audit import write_audit_log
from app.core.billing import execute_refund
from app.core.bridge_client import bridge_call
from app.core.rbac import CurrentAdmin, require_any_permission, require_permission
from app.db.database import get_db

router = APIRouter()

APPROVAL_TTL_HOURS = 24
ALLOWED_REQUEST_TYPES = ("org.delete", "billing.refund")


class ApprovalCreate(BaseModel):
    request_type: str
    resource_id: str
    payload: dict


class ApprovalReview(BaseModel):
    note: Optional[str] = None


async def _get_approval_or_404(db: AsyncSession, approval_id: UUID):
    row = (
        await db.execute(
            text(
                """
                SELECT id, request_type, resource_type, resource_id, payload,
                       status, requested_by_id, requested_by_email,
                       reviewed_by_email, expires_at, created_at
                FROM super_admin.approvals WHERE id = :approval_id
                """
            ),
            {"approval_id": approval_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return row


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["payload"] = row["payload"] if isinstance(row["payload"], dict) else {}
    return d


@router.post("")
async def create_approval(
    payload: ApprovalCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("orgs.delete")),
):
    if payload.request_type not in ALLOWED_REQUEST_TYPES:
        raise HTTPException(
            status_code=422, detail=f"Unsupported request_type: {payload.request_type}"
        )

    expires_at = datetime.now(timezone.utc) + timedelta(hours=APPROVAL_TTL_HOURS)
    approval_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.approvals
                    (request_type, resource_type, resource_id, payload, status,
                     requested_by_id, requested_by_email, expires_at)
                VALUES
                    (:request_type, 'organization', :resource_id, :payload, 'PENDING',
                     :admin_id, :admin_email, :expires_at)
                RETURNING id
                """
            ),
            {
                "request_type": payload.request_type,
                "resource_id": payload.resource_id,
                "payload": __import__("json").dumps(payload.payload),
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
        action="approval.request",
        resource_type="approval",
        resource_id=str(approval_id),
        new_values={
            "request_type": payload.request_type,
            "resource_id": payload.resource_id,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "approval_id": str(approval_id), "expires_at": expires_at}


@router.get("")
async def list_approvals(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_any_permission("orgs.delete", "billing.refund")),
):
    where = ""
    params: dict = {"limit": limit}
    if status:
        where = "WHERE status = :status"
        params["status"] = status.upper()
    rows = (
        await db.execute(
            text(
                f"""
                SELECT id, request_type, resource_type, resource_id, payload,
                       status, requested_by_id, requested_by_email,
                       reviewed_by_id, reviewed_by_email, review_note,
                       created_at, expires_at, reviewed_at, executed_at
                FROM super_admin.approvals
                {where}
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings()
    return {"items": [_row_to_dict(r) for r in rows]}


@router.post("/{approval_id}/approve")
async def approve_approval(
    approval_id: UUID,
    payload: ApprovalReview,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_any_permission("orgs.delete", "billing.refund")),
):
    row = await _get_approval_or_404(db, approval_id)
    if row["status"] != "PENDING":
        raise HTTPException(status_code=409, detail=f"Request is {row['status']}")
    if row["requested_by_id"] == admin.id:
        raise HTTPException(
            status_code=403, detail="Second-person rule: you cannot approve your own request"
        )
    if datetime.now(timezone.utc) > row["expires_at"]:
        await db.execute(
            text("UPDATE super_admin.approvals SET status = 'EXPIRED' WHERE id = :id"),
            {"id": approval_id},
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="Request expired (24h window)")

    await db.execute(
        text(
            """
            UPDATE super_admin.approvals
            SET status = 'APPROVED', reviewed_by_id = :admin_id,
                reviewed_by_email = :admin_email, review_note = :note, reviewed_at = now()
            WHERE id = :approval_id
            """
        ),
        {
            "admin_id": admin.id,
            "admin_email": admin.email,
            "note": payload.note,
            "approval_id": approval_id,
        },
    )
    await db.commit()

    # Execute the bridge call for the approved action.
    executed = False
    bridge_detail = None
    if row["request_type"] == "org.delete":
        try:
            await bridge_call(
                "POST",
                f"/orgs/{row['resource_id']}/delete",
                admin.email,
            )
            executed = True
        except HTTPException as exc:
            bridge_detail = exc.detail
    elif row["request_type"] == "billing.refund":
        row_payload = row["payload"] if isinstance(row["payload"], dict) else {}
        try:
            executed = await execute_refund(
                db,
                UUID(row_payload.get("invoice_id")),
                int(row_payload.get("amount_cents", 0)),
                reason=str(row_payload.get("reason", "approved refund"))[:200],
            )
        except Exception as exc:
            bridge_detail = str(exc)
    if executed:
        await db.execute(
            text(
                "UPDATE super_admin.approvals SET status = 'EXECUTED', "
                "executed_at = now() WHERE id = :approval_id"
            ),
            {"approval_id": approval_id},
        )
        await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="approval.approve" if executed else "approval.approve_failed",
        resource_type="approval",
        resource_id=str(approval_id),
        new_values={
            "request_type": row["request_type"],
            "resource_id": row["resource_id"],
            "executed": executed,
            "note": payload.note,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    if not executed:
        raise HTTPException(
            status_code=502,
            detail=f"Approved but bridge execution failed: {bridge_detail}",
        )
    return {"ok": True, "status": "EXECUTED"}


@router.post("/{approval_id}/reject")
async def reject_approval(
    approval_id: UUID,
    payload: ApprovalReview,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_any_permission("orgs.delete", "billing.refund")),
):
    row = await _get_approval_or_404(db, approval_id)
    if row["status"] != "PENDING":
        raise HTTPException(status_code=409, detail=f"Request is {row['status']}")
    if datetime.now(timezone.utc) > row["expires_at"]:
        status = "EXPIRED"
    else:
        status = "REJECTED"
    await db.execute(
        text(
            """
            UPDATE super_admin.approvals
            SET status = :status, reviewed_by_id = :admin_id,
                reviewed_by_email = :admin_email, review_note = :note, reviewed_at = now()
            WHERE id = :approval_id
            """
        ),
        {
            "status": status,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "note": payload.note,
            "approval_id": approval_id,
        },
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="approval.reject",
        resource_type="approval",
        resource_id=str(approval_id),
        new_values={
            "request_type": row["request_type"],
            "resource_id": row["resource_id"],
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "status": status}
