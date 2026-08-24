from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import verify_chain
from app.core.rbac import require_permission
from app.db.database import get_db

router = APIRouter()


@router.get("")
async def list_audit_logs(
    action: Optional[List[str]] = Query(None),
    actor_email: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    sort_by: str = Query("created_at", pattern="^created_at$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("audit.view")),
):
    where = []
    params: dict = {}
    if action:
        placeholders = ", ".join(f":ac{i}" for i in range(len(action)))
        where.append(f"action IN ({placeholders})")
        params.update({f"ac{i}": a for i, a in enumerate(action)})
    if actor_email:
        where.append("actor_email ILIKE :actor_email")
        params["actor_email"] = f"%{actor_email}%"
    if search:
        where.append(
            "(actor_email ILIKE :search OR action ILIKE :search OR resource_type ILIKE :search "
            "OR resource_id ILIKE :search OR ip_address ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    if from_date:
        where.append("created_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        where.append("created_at <= :to_date")
        params["to_date"] = to_date
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * page_size

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM super_admin.audit_logs {where_sql}"),
            params,
        )
    ).scalar_one()

    rows = (
        await db.execute(
            text(
                f"""
                SELECT id, actor_email, action, resource_type, resource_id,
                       old_values, new_values, ip_address, user_agent,
                       previous_hash, record_hash, created_at
                FROM super_admin.audit_logs
                {where_sql}
                ORDER BY created_at {sort_order.upper()}
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        )
    ).mappings().all()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/verify")
async def audit_chain_verify(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("audit.view")),
):
    """Recompute every record hash and detect any tampering."""
    return await verify_chain(db)
