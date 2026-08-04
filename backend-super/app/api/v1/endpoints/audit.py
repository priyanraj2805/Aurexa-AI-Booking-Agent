from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import verify_chain
from app.core.rbac import require_permission
from app.db.database import get_db

router = APIRouter()


@router.get("")
async def list_audit_logs(
    action: Optional[str] = Query(None),
    actor_email: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("audit.view")),
):
    where = []
    params: dict = {}
    if action:
        where.append("action = :action")
        params["action"] = action
    if actor_email:
        where.append("actor_email ILIKE :actor_email")
        params["actor_email"] = f"%{actor_email}%"

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT id, actor_email, action, resource_type, resource_id,
                       old_values, new_values, ip_address, user_agent,
                       previous_hash, record_hash, created_at
                FROM super_admin.audit_logs
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/verify")
async def audit_chain_verify(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("audit.view")),
):
    """Recompute every record hash and detect any tampering."""
    return await verify_chain(db)
