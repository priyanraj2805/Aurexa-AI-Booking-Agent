"""Org admin support request endpoints (Phase 12).

Forwarded to the control plane: a ticket is created in backend-super (where
the super admin team triages a single queue). Auth is the org admin's own
tenant JWT; the tenant backend resolves the org itself and presents the
shared service key so one org can never read or file another's tickets.
"""
import logging
from typing import List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

SERVICE_KEY_HEADER = "X-Support-Service-Key"


class SupportRequestCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    detail: str = Field(min_length=1, max_length=5000)
    priority: str = "normal"


async def _resolve_org(db: AsyncSession, user: User) -> dict:
    row = (
        await db.execute(
            text("SELECT id, name FROM organizations WHERE id = :org_id"),
            {"org_id": user.org_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"org_id": str(row["id"]), "org_name": row["name"]}


def _service_headers() -> dict[str, str]:
    return {"Content-Type": "application/json", SERVICE_KEY_HEADER: settings.SUPPORT_SERVICE_KEY}


def _raise_super_error(resp: httpx.Response) -> None:
    detail = "Support service unreachable"
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("detail"):
            detail = str(data["detail"])
    except Exception:
        pass
    raise HTTPException(status_code=resp.status_code if resp.status_code else 502, detail=detail)


@router.post("/requests")
async def create_support_request(
    payload: SupportRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = await _resolve_org(db, user)
    body = {
        "org_id": org["org_id"],
        "org_name": org["org_name"],
        "reporter_email": user.email,
        "reporter_name": user.full_name,
        "subject": payload.subject,
        "detail": payload.detail,
        "priority": payload.priority if payload.priority in ("low", "normal", "high", "urgent") else "normal",
    }
    try:
        async with httpx.AsyncClient(timeout=15, base_url=settings.SUPER_ADMIN_API_URL) as client:
            resp = await client.post(
                "/api/v1/support/tickets", headers=_service_headers(), json=body
            )
    except httpx.HTTPError as exc:
        logger.warning("Support ticket forward failed: %s", exc)
        raise HTTPException(status_code=502, detail="Support service unreachable")
    if resp.status_code >= 400:
        _raise_super_error(resp)
    return resp.json()


@router.get("/requests")
async def list_support_requests(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = await _resolve_org(db, user)
    try:
        async with httpx.AsyncClient(timeout=20, base_url=settings.SUPER_ADMIN_API_URL) as client:
            resp = await client.get(
                "/api/v1/support/tickets",
                headers=_service_headers(),
                params={"org_id": org["org_id"]},
            )
    except httpx.HTTPError as exc:
        logger.warning("Support ticket list forward failed: %s", exc)
        raise HTTPException(status_code=502, detail="Support service unreachable")
    if resp.status_code >= 400:
        _raise_super_error(resp)
    return resp.json()