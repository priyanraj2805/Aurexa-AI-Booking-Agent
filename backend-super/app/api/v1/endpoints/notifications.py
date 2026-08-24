"""Broadcast notifications (Phase 7).

Workflow: draft -> reviewed -> sent (or cancelled). Audience is resolved
when the broadcast is sent: all orgs, a selected list of orgs, or platform
staff. Every transition is audited.
"""
from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.inapp_notify import create_inapp_notification
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()

AUDIENCES = ("all_orgs", "selected_orgs", "staff")
STATUSES = ("draft", "reviewed", "sent", "cancelled")

BROADCAST_FIELDS = (
    "id, title, body, audience, org_ids, status, created_by_id, "
    "reviewed_by_id, reviewed_at, sent_by_id, sent_at, created_at, updated_at"
)

RECIPIENT_COUNT_SQL = {
    "all_orgs": "SELECT count(*) FROM public.organizations",
    "selected_orgs": None,  # len(org_ids)
    "staff": "SELECT count(*) FROM super_admin.super_admins WHERE status = 'active'",
}


class BroadcastCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=3)
    audience: str = "all_orgs"
    org_ids: list[str] = []


class BroadcastUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=255)
    body: Optional[str] = Field(None, min_length=3)
    audience: Optional[str] = None
    org_ids: Optional[list[str]] = None


async def _get_broadcast_or_404(db: AsyncSession, broadcast_id: UUID):
    row = (
        await db.execute(
            text(
                f"SELECT {BROADCAST_FIELDS} FROM super_admin.broadcast_notifications "
                "WHERE id = :broadcast_id"
            ),
            {"broadcast_id": broadcast_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    return row


def _validate_audience(payload) -> None:
    if payload.audience is not None and payload.audience not in AUDIENCES:
        raise HTTPException(
            status_code=422, detail=f"audience must be one of {', '.join(AUDIENCES)}"
        )
    if payload.audience == "selected_orgs" and not payload.org_ids:
        raise HTTPException(
            status_code=422, detail="selected_orgs requires at least one org_id"
        )
    if payload.audience in ("all_orgs", "staff") and payload.org_ids:
        raise HTTPException(
            status_code=422, detail="all_orgs/staff broadcasts must not carry org_ids"
        )
    for oid in payload.org_ids or []:
        try:
            UUID(str(oid))
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"Invalid org id: {oid}")


@router.get("")
async def list_broadcasts(
    status: Optional[List[str]] = Query(None),
    audience: Optional[List[str]] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    from_date: Optional[date] = Query(None, description="ISO date, e.g. 2026-01-01"),
    to_date: Optional[date] = Query(None, description="ISO date, e.g. 2026-12-31"),
    sort_by: str = Query("created_at", pattern="^(created_at|updated_at|sent_at|reviewed_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    _: CurrentAdmin = Depends(require_permission("notifications.view")),
    db: AsyncSession = Depends(get_db),
):
    where = []
    params: dict = {}
    if search:
        where.append("(title ILIKE :search OR body ILIKE :search)")
        params["search"] = f"%{search}%"
    if status:
        placeholders = ", ".join(f":st{i}" for i in range(len(status)))
        where.append(f"status IN ({placeholders})")
        params.update({f"st{i}": s for i, s in enumerate(status)})
    if audience:
        placeholders = ", ".join(f":au{i}" for i in range(len(audience)))
        where.append(f"audience IN ({placeholders})")
        params.update({f"au{i}": a for i, a in enumerate(audience)})
    if from_date:
        where.append("created_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        where.append("created_at <= :to_date")
        params["to_date"] = to_date
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    allowed_sort = {"created_at", "updated_at", "sent_at", "reviewed_at"}
    sort_col = sort_by if sort_by in allowed_sort else "created_at"
    sort_dir = sort_order.upper() if sort_order in ("asc", "desc") else "DESC"
    offset = (page - 1) * page_size
    total = (
        await db.execute(
            text(
                f"SELECT count(*) FROM super_admin.broadcast_notifications {where_sql}"
            ),
            params,
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {BROADCAST_FIELDS}
                FROM super_admin.broadcast_notifications
                {where_sql}
                ORDER BY {sort_col} {sort_dir}
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        )
    ).mappings().all()
    return {
        "items": [dict(r) for r in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


@router.get("/{broadcast_id}")
async def get_broadcast(
    broadcast_id: UUID,
    _: CurrentAdmin = Depends(require_permission("notifications.view")),
    db: AsyncSession = Depends(get_db),
):
    return dict(await _get_broadcast_or_404(db, broadcast_id))


@router.post("")
async def create_broadcast(
    payload: BroadcastCreate,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("notifications.manage")),
    db: AsyncSession = Depends(get_db),
):
    _validate_audience(payload)
    import json

    row = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.broadcast_notifications
                    (title, body, audience, org_ids, created_by_id)
                VALUES (:title, :body, :audience, CAST(:org_ids AS jsonb), :admin_id)
                RETURNING id
                """
            ),
            {
                "title": payload.title,
                "body": payload.body,
                "audience": payload.audience,
                "org_ids": json.dumps(payload.org_ids or []),
                "admin_id": admin.id,
            },
        )
    ).first()
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="notifications.create",
        resource_type="broadcast_notification",
        resource_id=str(row[0]),
        new_values={"title": payload.title, "audience": payload.audience},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "broadcast_id": str(row[0])}


@router.patch("/{broadcast_id}")
async def update_broadcast(
    broadcast_id: UUID,
    payload: BroadcastUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("notifications.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = await _get_broadcast_or_404(db, broadcast_id)
    if existing["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only drafts can be edited")
    _validate_audience(payload)

    import json

    updates = []
    params: dict = {"broadcast_id": broadcast_id}
    new_values: dict = {}
    for field in ("title", "body", "audience"):
        value = getattr(payload, field)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value
            new_values[field] = value
    if payload.org_ids is not None:
        updates.append("org_ids = :org_ids")
        params["org_ids"] = json.dumps(payload.org_ids)
        new_values["org_ids"] = payload.org_ids
    if not updates:
        return {"ok": True, "updated": []}
    await db.execute(
        text(
            f"UPDATE super_admin.broadcast_notifications SET {', '.join(updates)}, "
            "updated_at = now() WHERE id = :broadcast_id"
        ),
        params,
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="notifications.update",
        resource_type="broadcast_notification",
        resource_id=str(broadcast_id),
        new_values=new_values,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "updated": list(new_values.keys())}


async def _transition(
    db: AsyncSession,
    broadcast_id: UUID,
    current_status: str,
    target_status: str,
    request: Request,
    admin: CurrentAdmin,
    action: str,
    new_values: dict,
):
    existing = await _get_broadcast_or_404(db, broadcast_id)
    if existing["status"] != current_status:
        raise HTTPException(
            status_code=409,
            detail=f"Broadcast must be {current_status} (currently {existing['status']})",
        )
    sets = [f"status = '{target_status}'", "updated_at = now()"]
    extra: dict = {}
    if target_status == "reviewed":
        sets += ["reviewed_by_id = :actor", "reviewed_at = now()"]
        extra["actor"] = admin.id
    elif target_status == "sent":
        sets += ["sent_by_id = :actor", "sent_at = now()"]
        extra["actor"] = admin.id
    await db.execute(
        text(
            f"UPDATE super_admin.broadcast_notifications SET {', '.join(sets)} "
            "WHERE id = :broadcast_id"
        ),
        {"broadcast_id": broadcast_id, **extra},
    )
    await db.commit()
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action=action,
        resource_type="broadcast_notification",
        resource_id=str(broadcast_id),
        old_values={"status": current_status},
        new_values={"status": target_status, **new_values},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/{broadcast_id}/review")
async def review_broadcast(
    broadcast_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("notifications.manage")),
    db: AsyncSession = Depends(get_db),
):
    await _transition(
        db, broadcast_id, "draft", "reviewed", request, admin,
        "notifications.review", {},
    )
    return {"ok": True}


@router.post("/{broadcast_id}/send")
async def send_broadcast(
    broadcast_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("notifications.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = await _get_broadcast_or_404(db, broadcast_id)
    if existing["status"] != "reviewed":
        raise HTTPException(
            status_code=409,
            detail=f"Broadcast must be reviewed before sending (currently {existing['status']})",
        )

    count_sql = RECIPIENT_COUNT_SQL[existing["audience"]]
    if count_sql:
        recipient_count = (
            await db.execute(text(count_sql))
        ).scalar()
    else:
        recipient_count = len(existing["org_ids"] or [])

    await _transition(
        db, broadcast_id, "reviewed", "sent", request, admin,
        "notifications.send", {"recipient_count": int(recipient_count or 0)},
    )

    # Deliver to tenant orgs: one inbox row per (broadcast, org). Selected
    # orgs fall back to a per-org insert; "all orgs" is one set-based insert.
    audience = existing["audience"]
    if audience == "all_orgs":
        await db.execute(
            text(
                "INSERT INTO public.broadcast_deliveries (broadcast_id, org_id) "
                "SELECT :broadcast_id, id FROM public.organizations "
                "ON CONFLICT (broadcast_id, org_id) DO NOTHING"
            ),
            {"broadcast_id": broadcast_id},
        )
    elif audience == "selected_orgs" and existing["org_ids"]:
        for org_id in existing["org_ids"]:
            await db.execute(
                text(
                    "INSERT INTO public.broadcast_deliveries "
                    "(broadcast_id, org_id) "
                    "VALUES (:broadcast_id, :org_id) "
                    "ON CONFLICT (broadcast_id, org_id) DO NOTHING"
                ),
                {"broadcast_id": broadcast_id, "org_id": org_id},
            )
    elif audience == "staff":
        staff_rows = (
            await db.execute(
                text(
                    "SELECT id FROM super_admin.super_admins "
                    "WHERE status = 'active'"
                )
            )
        ).fetchall()
        for row in staff_rows:
            await create_inapp_notification(
                db,
                admin_id=row[0],
                kind="broadcast.staff",
                title=existing["title"][:255],
                body=(existing["body"] or "")[:4000],
                resource_type="broadcast_notification",
                resource_id=str(broadcast_id),
            )
    await db.commit()

    return {"ok": True, "recipient_count": int(recipient_count or 0)}


@router.post("/{broadcast_id}/cancel")
async def cancel_broadcast(
    broadcast_id: UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("notifications.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = await _get_broadcast_or_404(db, broadcast_id)
    if existing["status"] == "sent":
        raise HTTPException(status_code=409, detail="A sent broadcast cannot be cancelled")
    if existing["status"] == "cancelled":
        return {"ok": True, "already": True}
    await _transition(
        db, broadcast_id, existing["status"], "cancelled", request, admin,
        "notifications.cancel", {},
    )
    return {"ok": True}
