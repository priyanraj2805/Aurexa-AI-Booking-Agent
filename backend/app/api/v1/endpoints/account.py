"""Account & billing self-service for the admin panel.

The plan catalog, subscriptions and usage live in the control plane
(backend-super, shared Postgres). These endpoints act as an authenticated
proxy: the tenant user's session is validated here, and the control plane
is called with the shared service key.
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import deny_impersonation, get_current_user
from app.core.audit import log_audit_event
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.crud.user import update_user_password
from app.db.database import get_db
from app.models.user import User
from app.schemas.audit import AuditAction

router = APIRouter()
logger = logging.getLogger(__name__)


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=128)


class PlanChange(BaseModel):
    plan_id: str


def _service_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Support-Service-Key": settings.SUPPORT_SERVICE_KEY,
    }


async def _call_control_plane(method: str, path: str, payload: Optional[dict] = None):
    try:
        async with httpx.AsyncClient(
            timeout=10, base_url=settings.SUPER_ADMIN_API_URL
        ) as client:
            resp = await client.request(
                method, path, json=payload, headers=_service_headers()
            )
    except httpx.HTTPError as exc:
        logger.error("Control-plane call failed (%s %s): %s", method, path, exc)
        raise HTTPException(
            status_code=502, detail="Billing service is temporarily unavailable"
        )
    if resp.status_code >= 400:
        detail = "Billing service error"
        try:
            body = resp.json()
            if isinstance(body.get("detail"), str):
                detail = body["detail"]
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)
    return resp.json()


@router.get("/plan")
async def get_plan(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Current plan, subscription, monthly usage and the plan catalog."""
    return await _call_control_plane(
        "GET", f"/api/v1/org-billing/plan?org_id={user.org_id}"
    )


@router.post("/plan")
async def change_plan(
    payload: PlanChange,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
):
    """Upgrade or downgrade the org's plan (self-service)."""
    result = await _call_control_plane(
        "POST",
        "/api/v1/org-billing/plan",
        {
            "org_id": str(user.org_id),
            "plan_id": payload.plan_id,
            "requested_by_email": user.email,
            "note": "self-service change from the admin panel",
        },
    )
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.PLAN_CHANGE,
        resource_type="organization",
        resource_id=str(user.org_id),
        old_values=None,
        new_values={"to_plan": result.get("plan_code"), "source": "admin_panel"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return result


@router.patch("/profile")
async def update_profile(
    payload: ProfileUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the user's display name."""
    user.full_name = payload.full_name.strip()
    await db.commit()
    await db.refresh(user)

    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.PROFILE_UPDATE,
        resource_type="user",
        resource_id=str(user.id),
        old_values=None,
        new_values={"full_name": user.full_name},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "full_name": user.full_name}


@router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the password after verifying the current one."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    await update_user_password(db, user, payload.new_password)

    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.AUTH_PASSWORD_CHANGE,
        resource_type="user",
        resource_id=str(user.id),
        old_values=None,
        new_values={"event": "password_changed"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "detail": "Password changed"}