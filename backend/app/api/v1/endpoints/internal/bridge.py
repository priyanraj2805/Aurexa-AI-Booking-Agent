"""Super Admin internal bridge — lifecycle writes from the control plane.

Path: /internal/bridge (registered ONLY when INTERNAL_BRIDGE_ENABLED=true;
the verify_bridge dependency additionally 404s when the gate is off).

Every action:
  - is HMAC-signed (app.core.bridge),
  - writes a tenant-side audit log entry (public.audit_logs) attributed to
    the signed admin email,
  - bumps a telemetry counter (gated by TELEMETRY_ENABLED).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.bridge import BridgeAuth, verify_bridge
from app.core.audit import log_audit_event_async
from app.core.telemetry import increment_usage
from app.core.config import settings
from app.crud.organization import create_organization
from app.db.database import get_db
from app.schemas.audit import AuditAction
from app.schemas.organization import OrganizationCreate

router = APIRouter()

BRIDGE_PREFIX = "/internal/bridge"


class OrgCreateRequest(BaseModel):
    name: str
    is_active: bool = True
    business_name: Optional[str] = None


class OrgConfigUpdateRequest(BaseModel):
    business_name: Optional[str] = None
    timezone: Optional[str] = None
    require_otp: Optional[bool] = None
    allow_same_day_booking: Optional[bool] = None
    slot_cutoff_minutes: Optional[int] = None
    max_booking_days_ahead: Optional[int] = None
    session_retention_days: Optional[int] = None


async def _get_org_or_404(db: AsyncSession, org_id: UUID):
    org = await db.execute(
        text("SELECT id, name, is_active FROM organizations WHERE id = :org_id"),
        {"org_id": org_id},
    )
    row = org.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return row


@router.post("/orgs/{org_id}/suspend")
async def suspend_org(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    org = await _get_org_or_404(db, org_id)
    if not org["is_active"]:
        return {"ok": True, "already": True}
    await db.execute(
        text("UPDATE organizations SET is_active = false, updated_at = now() WHERE id = :org_id"),
        {"org_id": org_id},
    )
    await db.commit()
    await log_audit_event_async(
        org_id=org_id,
        action=AuditAction.ORG_SUSPEND,
        resource_type="organization",
        resource_id=str(org_id),
        actor_email=auth.admin_email,
        old_values={"is_active": True},
        new_values={"is_active": False},
    )
    await increment_usage(org_id, "org.suspend")
    return {"ok": True}


@router.post("/orgs/{org_id}/restore")
async def restore_org(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    org = await _get_org_or_404(db, org_id)
    if org["is_active"]:
        return {"ok": True, "already": True}
    await db.execute(
        text("UPDATE organizations SET is_active = true, updated_at = now() WHERE id = :org_id"),
        {"org_id": org_id},
    )
    await db.commit()
    await log_audit_event_async(
        org_id=org_id,
        action=AuditAction.ORG_RESTORE,
        resource_type="organization",
        resource_id=str(org_id),
        actor_email=auth.admin_email,
        old_values={"is_active": False},
        new_values={"is_active": True},
    )
    await increment_usage(org_id, "org.restore")
    return {"ok": True}


@router.post("/orgs")
async def create_org(
    payload: OrgCreateRequest,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    org = await create_organization(
        db, OrganizationCreate(name=payload.name, is_active=payload.is_active)
    )
    if payload.business_name:
        await db.execute(
            text("UPDATE org_configs SET business_name = :bn WHERE org_id = :org_id"),
            {"bn": payload.business_name, "org_id": org.id},
        )
        await db.commit()
    await log_audit_event_async(
        org_id=org.id,
        action=AuditAction.ORG_CREATE,
        resource_type="organization",
        resource_id=str(org.id),
        actor_email=auth.admin_email,
        new_values={"name": payload.name, "is_active": payload.is_active},
    )
    await increment_usage(org.id, "org.create")
    return {"ok": True, "org_id": str(org.id), "api_key": org.api_key}


@router.patch("/orgs/{org_id}/config")
async def update_org_config(
    org_id: UUID,
    payload: OrgConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    await _get_org_or_404(db, org_id)
    fields = (
        "business_name",
        "timezone",
        "require_otp",
        "allow_same_day_booking",
        "slot_cutoff_minutes",
        "max_booking_days_ahead",
        "session_retention_days",
    )
    updates = []
    params: dict = {"org_id": org_id}
    new_values: dict = {}
    for field in fields:
        value = getattr(payload, field)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value
            new_values[field] = value
    if not updates:
        return {"ok": True, "updated": []}
    await db.execute(
        text(f"UPDATE org_configs SET {', '.join(updates)} WHERE org_id = :org_id"),
        params,
    )
    await db.commit()
    await log_audit_event_async(
        org_id=org_id,
        action=AuditAction.CONFIG_UPDATE,
        resource_type="org_config",
        resource_id=str(org_id),
        actor_email=auth.admin_email,
        new_values=new_values,
    )
    await increment_usage(org_id, "org.config.update")
    return {"ok": True, "updated": list(new_values.keys())}


@router.post("/orgs/{org_id}/delete")
async def delete_org(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    org = await _get_org_or_404(db, org_id)
    # All dependent tables cascade (FK ondelete=CASCADE), audit_logs keep a
    # copy because they are FK-less by design.
    await db.execute(
        text("DELETE FROM organizations WHERE id = :org_id"), {"org_id": org_id}
    )
    await db.commit()
    await log_audit_event_async(
        org_id=org_id,
        action=AuditAction.ORG_DELETE,
        resource_type="organization",
        resource_id=str(org_id),
        actor_email=auth.admin_email,
        old_values={"name": org["name"]},
    )
    await increment_usage(org_id, "org.delete")
    return {"ok": True}


async def _get_user_or_404(db: AsyncSession, user_id: UUID):
    row = (
        await db.execute(
            text(
                "SELECT id, org_id, email, is_active FROM users WHERE id = :user_id"
            ),
            {"user_id": user_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    user = await _get_user_or_404(db, user_id)
    if not user["is_active"]:
        return {"ok": True, "already": True}
    await db.execute(
        text("UPDATE users SET is_active = false WHERE id = :user_id"),
        {"user_id": user_id},
    )
    await db.commit()
    await log_audit_event_async(
        org_id=user["org_id"],
        action=AuditAction.USER_DISABLE,
        resource_type="user",
        resource_id=str(user_id),
        actor_email=auth.admin_email,
        old_values={"is_active": True},
        new_values={"is_active": False},
    )
    await increment_usage(user["org_id"], "user.disable")
    return {"ok": True}


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    user = await _get_user_or_404(db, user_id)
    if user["is_active"]:
        return {"ok": True, "already": True}
    await db.execute(
        text("UPDATE users SET is_active = true WHERE id = :user_id"),
        {"user_id": user_id},
    )
    await db.commit()
    await log_audit_event_async(
        org_id=user["org_id"],
        action=AuditAction.USER_ENABLE,
        resource_type="user",
        resource_id=str(user_id),
        actor_email=auth.admin_email,
        old_values={"is_active": False},
        new_values={"is_active": True},
    )
    await increment_usage(user["org_id"], "user.enable")
    return {"ok": True}

@router.get("/dependency-health")
async def bridge_dependency_health(
    request: Request,
    auth: BridgeAuth = Depends(verify_bridge),
):
    from app.core.health_status import build_dependency_health_payload

    _ = auth
    request_id = request.headers.get("x-request-id")
    trace_id = request.headers.get("x-trace-id")
    return await build_dependency_health_payload(
        request_id=request_id, trace_id=trace_id
    )


async def _get_alert_or_404(db: AsyncSession, alert_id: UUID):
    row = (
        await db.execute(
            text("SELECT id, org_id, component, severity, resolved_at, acknowledged_at FROM system_alerts WHERE id = :alert_id"),
            {"alert_id": alert_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    return row


@router.post("/alerts/{alert_id}/acknowledge")
async def bridge_acknowledge_alert(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    alert = await _get_alert_or_404(db, alert_id)
    if alert["acknowledged_at"]:
        return {"ok": True, "already": True}
    await db.execute(
        text(
            "UPDATE system_alerts SET acknowledged_at = now(), acknowledged_by_user_id = NULL "
            "WHERE id = :alert_id"
        ),
        {"alert_id": alert_id},
    )
    await db.commit()
    await log_audit_event_async(
        org_id=alert["org_id"],
        action=AuditAction.ALERT_ACKNOWLEDGE,
        resource_type="system_alert",
        resource_id=str(alert_id),
        actor_email=auth.admin_email,
        new_values={"component": alert["component"]},
    )
    return {"ok": True}


@router.post("/alerts/{alert_id}/resolve")
async def bridge_resolve_alert(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: BridgeAuth = Depends(verify_bridge),
):
    alert = await _get_alert_or_404(db, alert_id)
    if alert["resolved_at"]:
        return {"ok": True, "already": True}
    await db.execute(
        text("UPDATE system_alerts SET resolved_at = now() WHERE id = :alert_id"),
        {"alert_id": alert_id},
    )
    await db.commit()
    await log_audit_event_async(
        org_id=alert["org_id"],
        action=AuditAction.ALERT_RESOLVE,
        resource_type="system_alert",
        resource_id=str(alert_id),
        actor_email=auth.admin_email,
        new_values={"component": alert["component"]},
    )
    return {"ok": True}
