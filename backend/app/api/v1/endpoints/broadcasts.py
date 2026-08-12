"""Broadcast inbox for tenant org admins.

Super-admin broadcasts are delivered as rows in `public.broadcast_deliveries`
(one per broadcast + org) when the broadcast is sent. This module lets an org
admin list their org's deliveries and mark them read per user.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.db.database import get_db
from app.models.user import User

router = APIRouter()

# Joined view of a delivery + its broadcast row (sent broadcasts only).
DELIVERY_FIELDS = (
    "d.id, d.broadcast_id, b.title, b.body, b.sent_at, d.created_at"
)


@router.get("/unread-count")
async def broadcast_unread_count(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight unread count for badge polling."""
    unread_count = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM public.broadcast_deliveries d
                LEFT JOIN public.broadcast_reads r
                    ON r.delivery_id = d.id AND r.user_id = :user_id
                WHERE d.org_id = :org_id AND r.user_id IS NULL
                """
            ),
            {"org_id": user.org_id, "user_id": user.id},
        )
    ).scalar()
    return {"unread_count": int(unread_count or 0)}


@router.get("")
async def list_broadcasts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List broadcasts delivered to the current admin's organization."""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {DELIVERY_FIELDS},
                       EXISTS (
                           SELECT 1 FROM public.broadcast_reads r
                           WHERE r.delivery_id = d.id AND r.user_id = :user_id
                       ) AS read
                FROM public.broadcast_deliveries d
                JOIN super_admin.broadcast_notifications b ON b.id = d.broadcast_id
                WHERE d.org_id = :org_id
                ORDER BY d.created_at DESC
                """
            ),
            {"org_id": user.org_id, "user_id": user.id},
        )
    ).fetchall()

    unread_count = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM public.broadcast_deliveries d
                LEFT JOIN public.broadcast_reads r
                    ON r.delivery_id = d.id AND r.user_id = :user_id
                WHERE d.org_id = :org_id AND r.user_id IS NULL
                """
            ),
            {"org_id": user.org_id, "user_id": user.id},
        )
    ).scalar()

    return {
        "items": [
            {
                "id": str(r[0]),
                "broadcast_id": str(r[1]),
                "title": r[2],
                "body": r[3],
                "sent_at": r[4].isoformat() if r[4] else None,
                "created_at": r[5].isoformat() if r[5] else None,
                "read": bool(r[6]),
            }
            for r in rows
        ],
        "unread_count": int(unread_count or 0),
    }


@router.post("/{delivery_id}/read")
async def mark_broadcast_read(
    delivery_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark one delivered broadcast as read for the current admin."""
    delivery = (
        await db.execute(
            text(
                "SELECT id FROM public.broadcast_deliveries "
                "WHERE id = :delivery_id AND org_id = :org_id"
            ),
            {"delivery_id": delivery_id, "org_id": user.org_id},
        )
    ).fetchone()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    await db.execute(
        text(
            "INSERT INTO public.broadcast_reads (delivery_id, user_id) "
            "VALUES (:delivery_id, :user_id) "
            "ON CONFLICT (delivery_id, user_id) DO NOTHING"
        ),
        {"delivery_id": delivery_id, "user_id": user.id},
    )
    await db.commit()
    return {"ok": True}
