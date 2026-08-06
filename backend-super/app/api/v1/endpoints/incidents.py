"""Incident management (Phase 6) — platform-scoped, lives in super_admin schema.

An incident is a tracked operational event (severity critical/major/minor)
that can be promoted from an alert or filed manually, then worked to
resolution.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.incident_notify import (
    notify_incident_created,
    notify_incident_escalated,
)
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()

SEVERITIES = ("critical", "major", "minor")
STATUSES = ("open", "investigating", "resolved")


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    severity: str
    component: str = Field(max_length=100)
    message: str = Field(default="", max_length=2000)


class IncidentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    severity: Optional[str] = None
    status: Optional[str] = None
    component: Optional[str] = Field(None, max_length=100)
    message: Optional[str] = Field(None, max_length=2000)


def _bad_severity(v: str) -> bool:
    return v not in SEVERITIES


def _bad_status(v: str) -> bool:
    return v not in STATUSES


@router.get("")
async def list_incidents(
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    component: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    _: CurrentAdmin = Depends(require_permission("incidents.view")),
    db: AsyncSession = Depends(get_db),
):
    where = []
    params: dict = {}
    if severity:
        if _bad_severity(severity):
            raise HTTPException(400, "Invalid severity")
        where.append("i.severity = :severity")
        params["severity"] = severity
    if status:
        if _bad_status(status):
            raise HTTPException(400, "Invalid status")
        where.append("i.status = :status")
        params["status"] = status
    if component:
        where.append("i.component = :component")
        params["component"] = component
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * page_size

    total = (
        await db.execute(
            text(f"SELECT count(*) FROM super_admin.incidents i {where_sql}"), params
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                f"""
                SELECT i.id, i.title, i.severity, i.status, i.component,
                       i.message, i.created_by_id, i.resolved_by_id,
                       i.resolved_at, i.created_at, i.updated_at
                FROM super_admin.incidents i
                {where_sql}
                ORDER BY i.created_at DESC
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


@router.post("")
async def create_incident(
    payload: IncidentCreate,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("incidents.manage")),
    db: AsyncSession = Depends(get_db),
):
    if _bad_severity(payload.severity):
        raise HTTPException(400, "Invalid severity")
    row = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.incidents
                    (title, severity, status, component, message, created_by_id)
                VALUES (:title, :severity, 'open', :component, :message, :created_by_id)
                RETURNING id, created_at
                """
            ),
            {
                "title": payload.title,
                "severity": payload.severity,
                "component": payload.component,
                "message": payload.message,
                "created_by_id": admin.id,
            },
        )
    ).mappings().first()
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="incidents.create",
        resource_type="incident",
        resource_id=str(row["id"]),
        new_values={
            "title": payload.title,
            "severity": payload.severity,
            "component": payload.component,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    notify_incident_created(
        incident_id=str(row["id"]),
        title=payload.title,
        severity=payload.severity,
        component=payload.component,
        message=payload.message,
    )
    return {"ok": True, "incident_id": str(row["id"])}


@router.get("/{incident_id}")
async def get_incident(
    incident_id: UUID,
    _: CurrentAdmin = Depends(require_permission("incidents.view")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, title, severity, status, component, message,
                       created_by_id, resolved_by_id, resolved_at,
                       created_at, updated_at
                FROM super_admin.incidents WHERE id = :incident_id
                """
            ),
            {"incident_id": incident_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return dict(row)


@router.patch("/{incident_id}")
async def update_incident(
    incident_id: UUID,
    payload: IncidentUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("incidents.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text(
                "SELECT id, title, severity, status, component, resolved_at FROM super_admin.incidents WHERE id = :incident_id"
            ),
            {"incident_id": incident_id},
        )
    ).mappings().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Incident not found")

    changes = payload.model_dump(exclude_unset=True)
    if "severity" in changes and _bad_severity(changes["severity"]):
        raise HTTPException(400, "Invalid severity")
    if "status" in changes and _bad_status(changes["status"]):
        raise HTTPException(400, "Invalid status")

    will_resolve = changes.get("status") == "resolved" and not existing["resolved_at"]
    will_unresolve = (
        "status" in changes
        and changes["status"] != "resolved"
        and existing["resolved_at"] is not None
    )
    will_escalate = (
        "severity" in changes
        and changes["severity"] in ("critical", "major")
        and changes["severity"] != existing["severity"]
    )

    sets = []
    params: dict = {"incident_id": incident_id}
    for col in ("title", "severity", "status", "component", "message"):
        if col in changes:
            sets.append(f"{col} = :{col}")
            params[col] = changes[col]
    if will_resolve:
        sets.append("resolved_by_id = :resolved_by_id")
        sets.append("resolved_at = now()")
        params["resolved_by_id"] = admin.id
    elif will_unresolve:
        sets.append("resolved_by_id = NULL")
        sets.append("resolved_at = NULL")
    if not sets:
        return {"ok": True}
    sets.append("updated_at = now()")

    await db.execute(
        text(
            f"UPDATE super_admin.incidents SET {', '.join(sets)} WHERE id = :incident_id"
        ),
        params,
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="incidents.update",
        resource_type="incident",
        resource_id=str(incident_id),
        old_values={"status": existing["status"], "severity": existing["severity"]},
        new_values=changes,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    if will_escalate:
        notify_incident_escalated(
            incident_id=str(incident_id),
            title=changes.get("title") or existing["title"],
            severity=changes["severity"],
            component=changes.get("component") or existing["component"],
        )
    return {"ok": True, "incident_id": str(incident_id)}
