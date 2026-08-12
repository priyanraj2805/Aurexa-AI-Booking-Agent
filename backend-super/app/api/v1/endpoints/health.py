"""Platform health endpoints (Phase 6).

dependency-health proxies the tenant bridge (HMAC-signed) so the platform
sees the same checks as the tenant dashboard; overview aggregates incident
counts from this database.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bridge_client import bridge_call
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()


@router.get("/dependencies")
async def get_dependency_health(
    admin: CurrentAdmin = Depends(require_permission("health.view")),
    db: AsyncSession = Depends(get_db),
):
    """Live dependency checks (database, LLM, email) via the tenant bridge."""
    try:
        payload = await bridge_call("GET", "/dependency-health", admin.email)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach tenant dependency health: {exc}",
        )
    return payload


@router.get("/overview")
async def get_health_overview(
    _: CurrentAdmin = Depends(require_permission("health.view")),
    db: AsyncSession = Depends(get_db),
):
    open_incidents = (
        await db.execute(
            text(
                "SELECT count(*) FROM super_admin.incidents "
                "WHERE status IN ('open', 'investigating')"
            )
        )
    ).scalar()
    return {
        "open_incidents": int(open_incidents or 0),
    }
