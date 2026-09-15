"""Org-scoped in-app notification inbox for the admin panel bell.

Reads/writes org_notifications for the CURRENT user's org only.
Endpoints: list, unread-count, mark-one-read, mark-all-read.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func as sa_func, or_ as sa_or, update

from app.db.database import get_db
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.models.org_notification import OrgNotification

router = APIRouter()
logger = logging.getLogger(__name__)


def _billing_predicate():
    """Rows that belong to the Announcements surface, not booking activity.

    Mirrors AnnouncementBell.isBillingItem on the frontend — keep both in sync.
    """
    return sa_or(
        OrgNotification.kind.like("billing\\_%", escape="\\"),
        OrgNotification.resource_type == "subscription",
    )


@router.get("")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent notifications for the current org (newest first)."""
    stmt = (
        select(OrgNotification)
        .where(OrgNotification.org_id == user.org_id)
        .order_by(OrgNotification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(OrgNotification.is_read.is_(False))

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {
        "items": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "resource_type": n.resource_type,
                "resource_id": n.resource_id,
                "is_read": bool(n.is_read),
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "read_at": n.read_at.isoformat() if n.read_at else None,
            }
            for n in rows
        ]
    }


@router.get("/unread-count")
async def unread_count(
    exclude_billing: bool = Query(False),
    billing_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(sa_func.count())
        .select_from(OrgNotification)
        .where(OrgNotification.org_id == user.org_id, OrgNotification.is_read.is_(False))
    )
    if billing_only:
        stmt = stmt.where(_billing_predicate())
    elif exclude_billing:
        stmt = stmt.where(~_billing_predicate())
    count = (await db.execute(stmt)).scalar()
    return {"unread": int(count or 0)}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        update(OrgNotification)
        .where(
            OrgNotification.id == notification_id,
            OrgNotification.org_id == user.org_id,
            OrgNotification.is_read.is_(False),
        )
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(OrgNotification)
        .where(
            OrgNotification.org_id == user.org_id,
            OrgNotification.is_read.is_(False),
        )
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"ok": True}
