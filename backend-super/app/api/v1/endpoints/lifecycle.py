"""Lifecycle actions (Phase 4) — the control plane's write surface.

Every action:
  1. requires the matching RBAC permission,
  2. calls the main backend's HMAC-signed bridge,
  3. appends a super_admin.audit_logs entry for the acting admin.

The tenant-side audit entry is written by the bridge itself.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from uuid import UUID

from app.core.audit import write_audit_log
from app.core.bridge_client import bridge_call
from app.core.rbac import CurrentAdmin, require_permission

router = APIRouter()


class OrgCreatePayload(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    is_active: bool = True
    business_name: Optional[str] = None


class OrgConfigPayload(BaseModel):
    business_name: Optional[str] = None
    timezone: Optional[str] = None
    require_otp: Optional[bool] = None
    allow_same_day_booking: Optional[bool] = None
    slot_cutoff_minutes: Optional[int] = None
    max_booking_days_ahead: Optional[int] = None
    session_retention_days: Optional[int] = None


async def _run_bridge_action(
    *,
    admin: CurrentAdmin,
    request: Request,
    action: str,
    resource_type: str,
    resource_id: Optional[str],
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    new_values: Optional[dict] = None,
    old_values: Optional[dict] = None,
) -> dict:
    try:
        result = await bridge_call(method, path, admin.email, json_body=json_body)
    except HTTPException as exc:
        raise exc

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        old_values=old_values,
        new_values=new_values,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return result


@router.post("/orgs/{org_id}/suspend")
async def suspend_org(
    org_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("orgs.suspend")),
):
    return await _run_bridge_action(
        admin=admin,
        request=request,
        action="org.suspend",
        resource_type="organization",
        resource_id=str(org_id),
        method="POST",
        path=f"/orgs/{org_id}/suspend",
        new_values={"is_active": False},
    )


@router.post("/orgs/{org_id}/restore")
async def restore_org(
    org_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("orgs.restore")),
):
    return await _run_bridge_action(
        admin=admin,
        request=request,
        action="org.restore",
        resource_type="organization",
        resource_id=str(org_id),
        method="POST",
        path=f"/orgs/{org_id}/restore",
        new_values={"is_active": True},
    )


@router.post("/orgs")
async def create_org(
    payload: OrgCreatePayload,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("orgs.create")),
):
    result = await _run_bridge_action(
        admin=admin,
        request=request,
        action="org.create",
        resource_type="organization",
        resource_id=None,
        method="POST",
        path="/orgs",
        json_body=payload.model_dump(exclude_none=True),
        new_values=payload.model_dump(exclude_none=True),
    )
    return result


@router.patch("/orgs/{org_id}/config")
async def update_org_config(
    org_id: UUID,
    payload: OrgConfigPayload,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("orgs.update")),
):
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        return {"ok": True, "updated": []}
    return await _run_bridge_action(
        admin=admin,
        request=request,
        action="org.config.update",
        resource_type="org_config",
        resource_id=str(org_id),
        method="PATCH",
        path=f"/orgs/{org_id}/config",
        json_body=changes,
        new_values=changes,
    )


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("users.disable")),
):
    return await _run_bridge_action(
        admin=admin,
        request=request,
        action="user.disable",
        resource_type="user",
        resource_id=str(user_id),
        method="POST",
        path=f"/users/{user_id}/disable",
        new_values={"is_active": False},
    )


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("users.enable")),
):
    return await _run_bridge_action(
        admin=admin,
        request=request,
        action="user.enable",
        resource_type="user",
        resource_id=str(user_id),
        method="POST",
        path=f"/users/{user_id}/enable",
        new_values={"is_active": True},
    )
