"""Impersonation endpoints (Phase 8) — act as an org admin, fully audited.

Flow: support.impersonate + MFA step-up → bridge issues a 30-min tenant JWT
→ row in super_admin.impersonations (jti only — the raw token is returned
once and never persisted). Starting a new impersonation ends any prior
active one for the same admin (single active session). The tenant backend
revokes sessions on stop, so the token dies immediately.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.bridge_client import bridge_call
from app.core.rbac import CurrentAdmin, require_permission
from app.core.stepup import require_stepup
from app.db.database import get_db

router = APIRouter()


class ImpersonationStartRequest(BaseModel):
    org_id: UUID
    user_email: str


class ImpersonationStopRequest(BaseModel):
    pass


async def _end_active(db: AsyncSession, admin: CurrentAdmin, ended_by: UUID):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, token_jti FROM super_admin.impersonations
                WHERE admin_id = :admin_id AND status = 'active'
                """
            ),
            {"admin_id": admin.id},
        )
    ).mappings().all()
    for row in rows:
        try:
            await bridge_call(
                "POST",
                "/impersonate/stop",
                admin.email,
                json_body={"token_jti": row["token_jti"]},
            )
        except Exception:
            pass
        await db.execute(
            text(
                """
                UPDATE super_admin.impersonations
                SET status = 'ended', ended_at = now(), ended_by = :ended_by
                WHERE id = :id AND status = 'active'
                """
            ),
            {"id": row["id"], "ended_by": ended_by},
        )
        await write_audit_log(
            actor_email=admin.email,
            action="impersonation.end",
            resource_type="impersonation",
            resource_id=str(row["id"]),
            new_values={"reason": "superseded"},
        )


@router.post("/start")
async def start_impersonation(
    payload: ImpersonationStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    require_stepup(request, admin)

    org_name = (
        await db.execute(
            text("SELECT name FROM organizations WHERE id = :org_id"),
            {"org_id": payload.org_id},
        )
    ).scalar()
    if not org_name:
        raise HTTPException(status_code=404, detail="Organization not found")

    await _end_active(db, admin, admin.id)

    result = await bridge_call(
        "POST",
        "/impersonate/start",
        admin.email,
        json_body={
            "org_id": str(payload.org_id),
            "user_email": payload.user_email,
        },
    )
    jti = result["token_jti"]
    expires_at = datetime.fromisoformat(result["expires_at"])
    row_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.impersonations
                    (admin_id, org_id, org_name, user_email, token_jti,
                     status, expires_at)
                VALUES (:admin_id, :org_id, :org_name, :user_email, :jti,
                        'active', :expires_at)
                RETURNING id
                """
            ),
            {
                "admin_id": admin.id,
                "org_id": payload.org_id,
                "org_name": org_name,
                "user_email": result["user_email"],
                "jti": jti,
                "expires_at": expires_at,
            },
        )
    ).scalar()
    await write_audit_log(
        actor_email=admin.email,
        action="impersonation.start",
        resource_type="impersonation",
        resource_id=str(row_id),
        new_values={
            "org_id": str(payload.org_id),
            "org_name": org_name,
            "user_email": result["user_email"],
            "expires_at": result["expires_at"],
        },
    )
    await db.commit()
    return {
        "id": str(row_id),
        "org_id": str(payload.org_id),
        "org_name": org_name,
        "user_email": result["user_email"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": result["expires_at"],
        "token": result["token"],
    }


@router.post("/{impersonation_id}/stop")
async def stop_impersonation(
    impersonation_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, token_jti, status FROM super_admin.impersonations
                WHERE id = :id AND admin_id = :admin_id
                """
            ),
            {"id": impersonation_id, "admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Impersonation not found")
    if row["status"] != "active":
        return {"ok": True, "already": True}

    try:
        await bridge_call(
            "POST",
            "/impersonate/stop",
            admin.email,
            json_body={"token_jti": row["token_jti"]},
        )
    except Exception:
        pass
    await db.execute(
        text(
            """
            UPDATE super_admin.impersonations
            SET status = 'ended', ended_at = now(), ended_by = :ended_by
            WHERE id = :id AND status = 'active'
            """
        ),
        {"id": impersonation_id, "ended_by": admin.id},
    )
    await write_audit_log(
        actor_email=admin.email,
        action="impersonation.end",
        resource_type="impersonation",
        resource_id=str(impersonation_id),
    )
    await db.commit()
    return {"ok": True}


@router.get("/active")
async def get_active_impersonation(
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, org_id, org_name, user_email, started_at, expires_at
                FROM super_admin.impersonations
                WHERE admin_id = :admin_id AND status = 'active'
                  AND expires_at > now()
                ORDER BY started_at DESC LIMIT 1
                """
            ),
            {"admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "org_name": row["org_name"],
        "user_email": row["user_email"],
        "started_at": row["started_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
    }


@router.get("")
async def list_impersonations(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, org_name, user_email, status, started_at, expires_at, ended_at
                FROM super_admin.impersonations
                WHERE admin_id = :admin_id
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            {"admin_id": admin.id, "limit": min(limit, 200)},
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "org_name": r["org_name"],
            "user_email": r["user_email"],
            "status": r["status"],
            "started_at": r["started_at"].isoformat(),
            "expires_at": r["expires_at"].isoformat(),
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
        }
        for r in rows
    ]
