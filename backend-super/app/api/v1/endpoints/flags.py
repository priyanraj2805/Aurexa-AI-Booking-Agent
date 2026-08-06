"""Feature flag registry (Phase 7).

DB-backed flags with platform/org/plan scoping, canary rollout percent and a
kill-switch toggle. The tenant backend evaluates flags live (cached 15s);
this module is the control-plane management surface.
"""
import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.bridge_client import bridge_call
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()

SCOPES = ("platform", "org", "plan")
CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,98}$")

FLAG_FIELDS = (
    "id, code, name, description, enabled, scope, targeting, rollout_percent, "
    "updated_by_id, created_at, updated_at"
)


class FlagCreate(BaseModel):
    code: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=150)
    description: Optional[str] = None
    enabled: bool = False
    scope: str = "platform"
    targeting: dict = {}
    rollout_percent: int = Field(100, ge=0, le=100)


class FlagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    description: Optional[str] = None
    scope: Optional[str] = None
    targeting: Optional[dict] = None
    rollout_percent: Optional[int] = Field(None, ge=0, le=100)


class FlagToggle(BaseModel):
    enabled: bool


async def _validate_payload(db: AsyncSession, payload, code: Optional[str] = None):
    """Shared validation for create/update. Raises 422 with a clear detail."""
    if code is not None and not CODE_RE.match(code):
        raise HTTPException(
            status_code=422,
            detail="code must match ^[a-z0-9][a-z0-9._-]{1,98}$",
        )
    scope = payload.scope
    if scope is not None and scope not in SCOPES:
        raise HTTPException(
            status_code=422, detail=f"scope must be one of {', '.join(SCOPES)}"
        )
    if payload.rollout_percent is not None and not (0 <= payload.rollout_percent <= 100):
        raise HTTPException(status_code=422, detail="rollout_percent must be 0-100")

    targeting = payload.targeting if payload.targeting is not None else {}
    if not isinstance(targeting, dict):
        raise HTTPException(status_code=422, detail="targeting must be an object")

    # Plan scope: every plan code must exist
    plan_codes = targeting.get("plan_codes") or []
    if scope == "plan" or plan_codes:
        if not isinstance(plan_codes, list) or not plan_codes:
            raise HTTPException(
                status_code=422, detail="plan scope requires targeting.plan_codes"
            )
        for pc in plan_codes:
            exists = (
                await db.execute(
                    text("SELECT 1 FROM super_admin.plans WHERE code = :pc"),
                    {"pc": pc},
                )
            ).first()
            if not exists:
                raise HTTPException(
                    status_code=422, detail=f"Unknown plan code: {pc}"
                )

    # Org scope: every id must be a well-formed UUID
    org_ids = targeting.get("org_ids") or []
    if scope == "org" or org_ids:
        if not isinstance(org_ids, list) or not org_ids:
            raise HTTPException(
                status_code=422, detail="org scope requires targeting.org_ids"
            )
        for oid in org_ids:
            try:
                UUID(str(oid))
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=422, detail=f"Invalid org id in targeting: {oid}"
                )

    if scope == "platform" and (org_ids or plan_codes):
        raise HTTPException(
            status_code=422, detail="platform scope must not carry org/plan targeting"
        )


@router.get("")
async def list_flags(
    scope: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
    _: CurrentAdmin = Depends(require_permission("flags.view")),
    db: AsyncSession = Depends(get_db),
):
    where = []
    params: dict = {}
    if scope:
        where.append("scope = :scope")
        params["scope"] = scope
    if enabled is not None:
        where.append("enabled = :enabled")
        params["enabled"] = enabled
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {FLAG_FIELDS}
                FROM super_admin.feature_flags
                {where_sql}
                ORDER BY created_at ASC
                """
            ),
            params,
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/{flag_id}")
async def get_flag(
    flag_id: UUID,
    _: CurrentAdmin = Depends(require_permission("flags.view")),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                f"SELECT {FLAG_FIELDS} FROM super_admin.feature_flags WHERE id = :flag_id"
            ),
            {"flag_id": flag_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found")
    return dict(row)


@router.post("")
async def create_flag(
    payload: FlagCreate,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("flags.manage")),
    db: AsyncSession = Depends(get_db),
):
    await _validate_payload(db, payload, code=payload.code)
    dup = (
        await db.execute(
            text("SELECT 1 FROM super_admin.feature_flags WHERE code = :code"),
            {"code": payload.code},
        )
    ).first()
    if dup:
        raise HTTPException(status_code=409, detail="Flag code already exists")

    import json

    row = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.feature_flags
                    (code, name, description, enabled, scope, targeting, rollout_percent, updated_by_id)
                VALUES (:code, :name, :description, :enabled, :scope, CAST(:targeting AS jsonb),
                        :rollout_percent, :admin_id)
                RETURNING id
                """
            ),
            {
                "code": payload.code,
                "name": payload.name,
                "description": payload.description,
                "enabled": payload.enabled,
                "scope": payload.scope,
                "targeting": json.dumps(payload.targeting or {}),
                "rollout_percent": payload.rollout_percent,
                "admin_id": admin.id,
            },
        )
    ).first()
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="flags.create",
        resource_type="feature_flag",
        resource_id=str(row[0]),
        new_values={
            "code": payload.code,
            "scope": payload.scope,
            "enabled": payload.enabled,
            "rollout_percent": payload.rollout_percent,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "flag_id": str(row[0])}


@router.patch("/{flag_id}")
async def update_flag(
    flag_id: UUID,
    payload: FlagUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("flags.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text(
                "SELECT id, code, name, description, enabled, scope, targeting, rollout_percent "
                "FROM super_admin.feature_flags WHERE id = :flag_id"
            ),
            {"flag_id": flag_id},
        )
    ).mappings().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Flag not found")

    await _validate_payload(db, payload)

    import json

    updates = []
    params: dict = {"flag_id": flag_id, "admin_id": admin.id}
    new_values: dict = {}
    for field in ("name", "description", "scope", "rollout_percent"):
        value = getattr(payload, field)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value
            new_values[field] = value
    if payload.targeting is not None:
        updates.append("targeting = :targeting")
        params["targeting"] = json.dumps(payload.targeting)
        new_values["targeting"] = payload.targeting
    if not updates:
        return {"ok": True, "updated": []}
    updates.append("updated_by_id = :admin_id")
    await db.execute(
        text(
            f"UPDATE super_admin.feature_flags SET {', '.join(updates)}, "
            "updated_at = now() WHERE id = :flag_id"
        ),
        params,
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="flags.update",
        resource_type="feature_flag",
        resource_id=str(flag_id),
        old_values={
            "name": existing["name"],
            "scope": existing["scope"],
            "enabled": existing["enabled"],
            "rollout_percent": existing["rollout_percent"],
        },
        new_values=new_values,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "updated": list(new_values.keys())}


@router.post("/{flag_id}/toggle")
async def toggle_flag(
    flag_id: UUID,
    payload: FlagToggle,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("flags.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text(
                "SELECT id, code, enabled FROM super_admin.feature_flags WHERE id = :flag_id"
            ),
            {"flag_id": flag_id},
        )
    ).mappings().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Flag not found")
    if existing["enabled"] == payload.enabled:
        return {"ok": True, "already": True, "enabled": payload.enabled}
    await db.execute(
        text(
            "UPDATE super_admin.feature_flags SET enabled = :enabled, "
            "updated_by_id = :admin_id, updated_at = now() WHERE id = :flag_id"
        ),
        {"flag_id": flag_id, "enabled": payload.enabled, "admin_id": admin.id},
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="flags.toggle",
        resource_type="feature_flag",
        resource_id=str(flag_id),
        old_values={"enabled": existing["enabled"]},
        new_values={"enabled": payload.enabled, "code": existing["code"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "enabled": payload.enabled}


@router.delete("/{flag_id}")
async def delete_flag(
    flag_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("flags.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            text(
                "SELECT id, code FROM super_admin.feature_flags WHERE id = :flag_id"
            ),
            {"flag_id": flag_id},
        )
    ).mappings().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Flag not found")
    await db.execute(
        text("DELETE FROM super_admin.feature_flags WHERE id = :flag_id"),
        {"flag_id": flag_id},
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="flags.delete",
        resource_type="feature_flag",
        resource_id=str(flag_id),
        old_values={"code": existing["code"]},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True}


@router.get("/{flag_id}/evaluate")
async def evaluate_flag(
    flag_id: UUID,
    org_id: Optional[UUID] = Query(None),
    admin: CurrentAdmin = Depends(require_permission("flags.view")),
    db: AsyncSession = Depends(get_db),
):
    """Live evaluation from the tenant backend via the internal bridge.

    Shows what the main backend actually serves right now (after its cache
    TTL) for the flag — including canary buckets and kill switches.
    """
    row = (
        await db.execute(
            text("SELECT id, code FROM super_admin.feature_flags WHERE id = :flag_id"),
            {"flag_id": flag_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found")
    try:
        result = await bridge_call(
            "GET",
            f"/flags/{row['code']}" + (f"/org/{org_id}" if org_id else ""),
            admin.email,
        )
    except HTTPException as exc:
        raise exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bridge call failed: {exc}")
    return result
