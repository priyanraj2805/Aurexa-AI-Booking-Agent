"""Platform-wide system alerts (Phase 6).

Reads public.system_alerts directly; acknowledge/resolve write through the
tenant bridge so the main backend keeps its audit trail and usage counters.
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.bridge_client import bridge_call
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()

SEVERITIES = ("info", "warning", "critical")


@router.get("")
async def list_alerts(
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="open | resolved"),
    component: Optional[str] = Query(None),
    org_id: Optional[UUID] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    _: CurrentAdmin = Depends(require_permission("alerts.view")),
    db: AsyncSession = Depends(get_db),
):
    where = []
    params: dict = {}
    if severity:
        where.append("a.severity = :severity")
        params["severity"] = severity
    if status == "open":
        where.append("a.resolved_at IS NULL")
    elif status == "resolved":
        where.append("a.resolved_at IS NOT NULL")
    if component:
        where.append("a.component = :component")
        params["component"] = component
    if org_id:
        where.append("a.org_id = :org_id")
        params["org_id"] = org_id
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * page_size

    total = (
        await db.execute(
            text(f"SELECT count(*) FROM public.system_alerts a {where_sql}"), params
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                f"""
                SELECT a.id, a.org_id, o.name AS org_name, a.component,
                       a.severity, a.code, a.message, a.occurrence_count,
                       a.created_at, a.acknowledged_at, a.resolved_at
                FROM public.system_alerts a
                LEFT JOIN public.organizations o ON o.id = a.org_id
                {where_sql}
                ORDER BY a.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        )
    ).mappings().all()
    return {
        "items": [dict(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


@router.get("/trends")
async def alert_trends(
    days: int = Query(30, ge=1, le=90),
    _: CurrentAdmin = Depends(require_permission("alerts.view")),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        await db.execute(
            text(
                """
                SELECT date_trunc('day', created_at)::date AS day,
                       severity, count(*) AS cnt
                FROM public.system_alerts
                WHERE created_at >= :since
                GROUP BY 1, 2
                ORDER BY 1
                """
            ),
            {"since": since},
        )
    ).mappings().all()
    return {"days": days, "items": [dict(r) for r in rows]}


@router.get("/{alert_id}")
async def get_alert(
    alert_id: UUID,
    _: CurrentAdmin = Depends(require_permission("alerts.view")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                """
                SELECT a.id, a.org_id, o.name AS org_name, a.component,
                       a.severity, a.code, a.message, a.occurrence_count,
                       a.created_at, a.acknowledged_at, a.resolved_at
                FROM public.system_alerts a
                LEFT JOIN public.organizations o ON o.id = a.org_id
                WHERE a.id = :alert_id
                """
            ),
            {"alert_id": alert_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    return dict(row)


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("alerts.manage")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                "SELECT id, org_id, component, acknowledged_at FROM public.system_alerts WHERE id = :alert_id"
            ),
            {"alert_id": alert_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    if row["acknowledged_at"]:
        return {"ok": True, "already": True}
    try:
        await bridge_call(
            "POST", f"/alerts/{alert_id}/acknowledge", admin.email
        )
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bridge call failed: {exc}")
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="alerts.acknowledge",
        resource_type="system_alert",
        resource_id=str(alert_id),
        new_values={"component": row["component"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("alerts.manage")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                "SELECT id, org_id, component, resolved_at FROM public.system_alerts WHERE id = :alert_id"
            ),
            {"alert_id": alert_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    if row["resolved_at"]:
        return {"ok": True, "already": True}
    try:
        await bridge_call("POST", f"/alerts/{alert_id}/resolve", admin.email)
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bridge call failed: {exc}")
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="alerts.resolve",
        resource_type="system_alert",
        resource_id=str(alert_id),
        new_values={"component": row["component"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}
