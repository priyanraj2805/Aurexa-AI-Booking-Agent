"""Per-admin notification inbox (Phase 16).

Reads/writes super_admin.inapp_notifications for the CURRENT admin only
(rows are never addressable by another admin). Used by the dashboard bell:
unread count badge, recent list, mark-one-read, mark-all-read.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.inapp_notify import NOTIFICATION_FIELDS
from app.core.rbac import CurrentAdmin, get_current_admin
from app.db.database import get_db

router = APIRouter()


@router.get("")
async def list_inbox(
    unread_only: bool = Query(False),
    limit: int = Query(30, ge=1, le=100),
    admin: CurrentAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    where = "admin_id = :admin_id"
    params: dict = {"admin_id": admin.id, "limit": limit}
    if unread_only:
        where += " AND is_read = FALSE"
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {NOTIFICATION_FIELDS}
                FROM super_admin.inapp_notifications
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/unread-count")
async def unread_count(
    admin: CurrentAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    count = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM super_admin.inapp_notifications
                WHERE admin_id = :admin_id AND is_read = FALSE
                """
            ),
            {"admin_id": admin.id},
        )
    ).scalar()
    return {"unread": int(count or 0)}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: UUID,
    admin: CurrentAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            text(
                """
                UPDATE super_admin.inapp_notifications
                SET is_read = TRUE, read_at = now()
                WHERE id = :id AND admin_id = :admin_id AND is_read = FALSE
                RETURNING id
                """
            ),
            {"id": notification_id, "admin_id": admin.id},
        )
    ).mappings().first()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(
    admin: CurrentAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    count = (
        await db.execute(
            text(
                """
                UPDATE super_admin.inapp_notifications
                SET is_read = TRUE, read_at = now()
                WHERE admin_id = :admin_id AND is_read = FALSE
                RETURNING id
                """
            ),
            {"admin_id": admin.id},
        )
    ).all()
    await db.commit()
    return {"ok": True, "updated": len(count)}
