from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.pii import mask_value
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_readonly_db
from app.schemas.tenant import (
    BookingListResponse,
    BookingSummary,
    OrgAuditResponse,
    OrgConfigSummary,
    OrgDetail,
    OrgListResponse,
    OrgSummary,
    SessionListResponse,
    SessionSummary,
    StatsResponse,
    TopOrgsResponse,
    TopOrgRow,
    TrendPoint,
    UsageResponse,
    UserActivityResponse,
    UserListResponse,
    UserProfile,
    UserSummary,
)

router = APIRouter()


def _mask_api_key(key: str) -> str:
    if len(key) <= 12:
        return key[:4] + "…" if key else ""
    return key[:4] + "…" + key[-4:]


def _can_unmask(admin: CurrentAdmin, unmask: bool) -> bool:
    """True when the caller holds pii.unmask AND asked for unmasked data."""
    return unmask and admin.has_permission("pii.unmask")


def _masked_user_row(row: dict, can_unmask: bool) -> dict:
    out = dict(row)
    if not can_unmask:
        if out.get("email"):
            out["email"] = mask_value("email", out["email"])
        if out.get("full_name"):
            out["full_name"] = mask_value("full_name", out["full_name"])
    return out


def _masked_booking_rows(rows, can_unmask: bool) -> list:
    out = []
    for r in rows:
        row = dict(r)
        if not can_unmask:
            for key in ("customer_name", "customer_email", "customer_phone"):
                if row.get(key):
                    row[key] = mask_value(key, row[key])
        out.append(BookingSummary(**{k: row.get(k) for k in BookingSummary.model_fields}))
    return out


@router.get("/orgs", response_model=OrgListResponse)
async def list_organizations(
    search: Optional[str] = Query(None, max_length=100),
    is_active: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("orgs.view")),
):
    where = []
    params: dict = {}
    if search:
        where.append(
            "(o.name ILIKE :search OR COALESCE(c.business_name, '') ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    if is_active is not None:
        where.append("o.is_active = :is_active")
        params["is_active"] = is_active

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * page_size

    total = (
        await db.execute(
            text(
                f"""
                SELECT count(*) FROM organizations o
                LEFT JOIN org_configs c ON c.org_id = o.id
                {where_sql}
                """
            ),
            params,
        )
    ).scalar()

    rows = (
        await db.execute(
            text(
                f"""
                SELECT o.id, o.name, o.is_active, o.api_key, o.created_at,
                       c.business_name, c.session_retention_days,
                       (SELECT count(*) FROM users u WHERE u.org_id = o.id) AS user_count,
                       (SELECT count(*) FROM bookings b WHERE b.org_id = o.id) AS booking_count,
                       (SELECT count(*) FROM chat_sessions s WHERE s.org_id = o.id) AS conversation_count,
                       (SELECT max(updated_at) FROM chat_sessions s2 WHERE s2.org_id = o.id) AS last_activity_at
                FROM organizations o
                LEFT JOIN org_configs c ON c.org_id = o.id
                {where_sql}
                ORDER BY o.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset, **params},
        )
    ).mappings()

    items = [
        OrgSummary(
            id=r["id"],
            name=r["name"],
            business_name=r["business_name"],
            is_active=r["is_active"],
            api_key_masked=_mask_api_key(r["api_key"] or ""),
            created_at=r["created_at"],
            user_count=r["user_count"] or 0,
            booking_count=r["booking_count"] or 0,
            conversation_count=r["conversation_count"] or 0,
            session_retention_days=r["session_retention_days"],
            last_activity_at=r["last_activity_at"],
        )
        for r in rows
    ]
    return OrgListResponse(total=total or 0, page=page, page_size=page_size, items=items)


@router.get("/orgs/{org_id}", response_model=OrgDetail)
async def get_organization(
    org_id: UUID,
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("orgs.view")),
):
    row = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name, o.is_active, o.api_key, o.created_at,
                       c.business_name, c.session_retention_days, c.timezone,
                       c.require_otp, c.allow_same_day_booking, c.slot_cutoff_minutes,
                       c.max_booking_days_ahead, c.meeting_mode,
                       c.active_calendar_provider, c.active_meeting_provider,
                       c.whatsapp_enabled, c.google_calendar_enabled,
                       c.outlook_calendar_enabled, c.website_url,
                       (SELECT count(*) FROM users u WHERE u.org_id = o.id) AS user_count,
                       (SELECT count(*) FROM bookings b WHERE b.org_id = o.id) AS booking_count,
                       (SELECT count(*) FROM chat_sessions s WHERE s.org_id = o.id) AS conversation_count,
                       (SELECT max(updated_at) FROM chat_sessions s2 WHERE s2.org_id = o.id) AS last_activity_at
                FROM organizations o
                LEFT JOIN org_configs c ON c.org_id = o.id
                WHERE o.id = :org_id
                """
            ),
            {"org_id": org_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")

    config = OrgConfigSummary(
        timezone=row["timezone"],
        require_otp=bool(row["require_otp"]),
        allow_same_day_booking=bool(row["allow_same_day_booking"]),
        slot_cutoff_minutes=row["slot_cutoff_minutes"]
        if row["slot_cutoff_minutes"] is not None
        else 5,
        max_booking_days_ahead=row["max_booking_days_ahead"]
        if row["max_booking_days_ahead"] is not None
        else 90,
        session_retention_days=row["session_retention_days"]
        if row["session_retention_days"] is not None
        else 30,
        meeting_mode=row["meeting_mode"],
        active_calendar_provider=row["active_calendar_provider"],
        active_meeting_provider=row["active_meeting_provider"],
        whatsapp_enabled=bool(row["whatsapp_enabled"]),
        google_calendar_enabled=bool(row["google_calendar_enabled"]),
        outlook_calendar_enabled=bool(row["outlook_calendar_enabled"]),
        website_url=row["website_url"],
        business_name=row["business_name"],
    )
    return OrgDetail(
        id=row["id"],
        name=row["name"],
        business_name=row["business_name"],
        is_active=row["is_active"],
        api_key_masked=_mask_api_key(row["api_key"] or ""),
        created_at=row["created_at"],
        user_count=row["user_count"] or 0,
        booking_count=row["booking_count"] or 0,
        conversation_count=row["conversation_count"] or 0,
        session_retention_days=row["session_retention_days"],
        last_activity_at=row["last_activity_at"],
        config=config,
    )


@router.get("/orgs/{org_id}/bookings", response_model=BookingListResponse)
async def list_org_bookings(
    org_id: UUID,
    unmask: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("orgs.view")),
):
    exists = (
        await db.execute(
            text("SELECT 1 FROM organizations WHERE id = :org_id"), {"org_id": org_id}
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    can_unmask = _can_unmask(admin, unmask)
    offset = (page - 1) * page_size
    total = (
        await db.execute(
            text("SELECT count(*) FROM bookings WHERE org_id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                """
                SELECT id, customer_name, customer_email, customer_phone,
                       org_slot_start_at, org_slot_end_at, meeting_link_url,
                       meeting_type_snapshot, created_at
                FROM bookings
                WHERE org_id = :org_id
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"org_id": org_id, "limit": page_size, "offset": offset},
        )
    ).mappings()
    items = _masked_booking_rows(rows, can_unmask)
    return BookingListResponse(total=total or 0, page=page, page_size=page_size, items=items)


@router.get("/orgs/{org_id}/sessions", response_model=SessionListResponse)
async def list_org_sessions(
    org_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("orgs.view")),
):
    exists = (
        await db.execute(
            text("SELECT 1 FROM organizations WHERE id = :org_id"), {"org_id": org_id}
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    offset = (page - 1) * page_size
    total = (
        await db.execute(
            text("SELECT count(*) FROM chat_sessions WHERE org_id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                """
                SELECT s.id, s.channel, s.session_status, s.current_intent,
                       s.human_agent_active, s.created_at, s.updated_at,
                       (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
                FROM chat_sessions s
                WHERE s.org_id = :org_id
                ORDER BY s.updated_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"org_id": org_id, "limit": page_size, "offset": offset},
        )
    ).mappings()
    items = [SessionSummary(**{k: r.get(k) for k in SessionSummary.model_fields}) for r in rows]
    return SessionListResponse(total=total or 0, page=page, page_size=page_size, items=items)


@router.get("/users", response_model=UserListResponse)
async def search_users(
    search: Optional[str] = Query(None, max_length=100),
    org_id: Optional[UUID] = Query(None),
    is_active: Optional[bool] = Query(None),
    unmask: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("users.view")),
):
    where = []
    params: dict = {}
    if search:
        where.append("(u.email ILIKE :search OR u.full_name ILIKE :search)")
        params["search"] = f"%{search}%"
    if org_id is not None:
        where.append("u.org_id = :org_id")
        params["org_id"] = org_id
    if is_active is not None:
        where.append("u.is_active = :is_active")
        params["is_active"] = is_active

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * page_size

    total = (
        await db.execute(
            text(f"SELECT count(*) FROM users u {where_sql}"), params
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                f"""
                SELECT u.id, u.org_id, o.name AS org_name, u.email, u.full_name,
                       u.is_active, u.created_at
                FROM users u
                JOIN organizations o ON o.id = u.org_id
                {where_sql}
                ORDER BY u.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset, **params},
        )
    ).mappings()
    can_unmask = _can_unmask(admin, unmask)
    items = [
        UserSummary(**{k: r.get(k) for k in UserSummary.model_fields})
        for r in (_masked_user_row(dict(row), can_unmask) for row in rows)
    ]
    return UserListResponse(total=total or 0, page=page, page_size=page_size, items=items)


# ── Dashboard KPIs (Phase 3) ────────────────────────────────────────────────


@router.get("/stats", response_model=StatsResponse)
async def platform_stats(
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("dashboard.view")),
):
    async def scalar(sql: str, params: dict | None = None):
        return (await db.execute(text(sql), params or {})).scalar() or 0

    orgs_total = await scalar("SELECT count(*) FROM organizations")
    orgs_active = await scalar(
        "SELECT count(*) FROM organizations WHERE is_active = true"
    )
    users_total = await scalar("SELECT count(*) FROM users")
    users_active = await scalar("SELECT count(*) FROM users WHERE is_active = true")
    bookings_total = await scalar("SELECT count(*) FROM bookings")
    bookings_today = await scalar(
        "SELECT count(*) FROM bookings WHERE created_at::date = now()::date"
    )
    bookings_7d = await scalar(
        "SELECT count(*) FROM bookings WHERE created_at >= now() - interval '7 days'"
    )
    conversations_total = await scalar("SELECT count(*) FROM chat_sessions")
    conversations_active = await scalar(
        "SELECT count(*) FROM chat_sessions WHERE session_status = 'ACTIVE'"
    )
    conversations_today = await scalar(
        "SELECT count(*) FROM chat_sessions WHERE created_at::date = now()::date"
    )
    mrr_cents = (
        await db.execute(
            text(
                """
                SELECT COALESCE(SUM(p.monthly_price_cents), 0)
                FROM super_admin.subscriptions s
                JOIN super_admin.plans p ON p.id = s.plan_id
                WHERE s.status IN ('active', 'past_due', 'trialing')
                """
            )
        )
    ).scalar() or 0

    trend_rows = (
        await db.execute(
            text(
                """
                SELECT d::date AS day,
                       count(DISTINCT b.id) AS bookings,
                       count(DISTINCT s.id) AS conversations
                FROM generate_series(now()::date - (:days - 1) * interval '1 day', now()::date, '1 day') AS d
                LEFT JOIN bookings b ON b.created_at::date = d
                LEFT JOIN chat_sessions s ON s.created_at::date = d
                GROUP BY d
                ORDER BY d
                """
            ),
            {"days": days},
        )
    ).mappings()
    trend = [
        TrendPoint(
            date=str(r["day"]),
            bookings=r["bookings"] or 0,
            conversations=r["conversations"] or 0,
        )
        for r in trend_rows
    ]

    return StatsResponse(
        orgs_total=orgs_total,
        orgs_active=orgs_active,
        orgs_suspended=orgs_total - orgs_active,
        users_total=users_total,
        users_active=users_active,
        bookings_total=bookings_total,
        bookings_today=bookings_today,
        bookings_7d=bookings_7d,
        conversations_total=conversations_total,
        conversations_active=conversations_active,
        conversations_today=conversations_today,
        mrr=int(mrr_cents),
        ai_tokens_30d=None,
        trend=trend,
    )


@router.get("/stats/top-orgs", response_model=TopOrgsResponse)
async def top_orgs(
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("dashboard.view")),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name, o.is_active,
                       (SELECT count(*) FROM bookings b WHERE b.org_id = o.id) AS booking_count,
                       (SELECT count(*) FROM chat_sessions s WHERE s.org_id = o.id) AS conversation_count
                FROM organizations o
                ORDER BY booking_count DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    ).mappings()

    # Per-org daily booking counts over the trend window (zero-filled).
    daily_rows = (
        await db.execute(
            text(
                """
                SELECT b.org_id, d::date AS day, count(DISTINCT b.id) AS n
                FROM generate_series(
                         now()::date - (:days - 1) * interval '1 day',
                         now()::date,
                         '1 day'
                     ) AS d
                LEFT JOIN bookings b ON b.created_at::date = d
                WHERE d IN (SELECT DISTINCT b2.created_at::date FROM bookings b2)
                   OR d = now()::date
                GROUP BY b.org_id, d
                ORDER BY d
                """
            ),
            {"days": days},
        )
    ).mappings()

    by_org: dict[UUID, dict[str, int]] = {}
    for r in daily_rows:
        by_org.setdefault(
            r["org_id"], {}
        ).setdefault(str(r["day"]), 0)
        by_org[r["org_id"]][str(r["day"])] = int(r["n"] or 0)

    today = datetime.utcnow().date()
    items = []
    for r in rows:
        day_map = by_org.get(r["id"], {})
        trend = [
            day_map.get(str(today - timedelta(days=days - 1 - i)), 0)
            for i in range(days)
        ]
        items.append(TopOrgRow(**{**dict(r), "trend": trend}))

    return TopOrgsResponse(items=items)


# ── Org detail tabs (Phase 3) ───────────────────────────────────────────────


@router.get("/orgs/{org_id}/users", response_model=UserListResponse)
async def list_org_users(
    org_id: UUID,
    unmask: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("users.view")),
):
    exists = (
        await db.execute(
            text("SELECT 1 FROM organizations WHERE id = :org_id"), {"org_id": org_id}
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    can_unmask = _can_unmask(admin, unmask)
    offset = (page - 1) * page_size
    total = (
        await db.execute(
            text("SELECT count(*) FROM users WHERE org_id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id, u.org_id, o.name AS org_name, u.email, u.full_name,
                       u.is_active, u.created_at
                FROM users u
                JOIN organizations o ON o.id = u.org_id
                WHERE u.org_id = :org_id
                ORDER BY u.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"org_id": org_id, "limit": page_size, "offset": offset},
        )
    ).mappings()
    items = [
        UserSummary(**{k: r.get(k) for k in UserSummary.model_fields})
        for r in (_masked_user_row(dict(row), can_unmask) for row in rows)
    ]
    return UserListResponse(total=total or 0, page=page, page_size=page_size, items=items)


@router.get("/orgs/{org_id}/usage", response_model=UsageResponse)
async def org_usage(
    org_id: UUID,
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("dashboard.view")),
):
    exists = (
        await db.execute(
            text("SELECT 1 FROM organizations WHERE id = :org_id"), {"org_id": org_id}
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    rows = (
        await db.execute(
            text(
                """
                SELECT metric, sum(count) AS count, max(updated_at) AS last_updated
                FROM super_admin.usage_events
                WHERE org_id = :org_id
                  AND metric IN ('bookings', 'ai_tokens', 'conversations')
                GROUP BY metric
                ORDER BY metric
                """
            ),
            {"org_id": org_id},
        )
    ).mappings()
    return UsageResponse(
        items=[
            {"metric": r["metric"], "count": r["count"] or 0, "last_updated": r["last_updated"]}
            for r in rows
        ]
    )


@router.get("/orgs/{org_id}/audit", response_model=OrgAuditResponse)
async def org_audit(
    org_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("audit.view")),
):
    """Control-plane actions that touched this org (super_admin.audit_logs)."""
    total = (
        await db.execute(
            text(
                "SELECT count(*) FROM super_admin.audit_logs "
                "WHERE resource_type = 'organization' AND resource_id = :org_id"
            ),
            {"org_id": str(org_id)},
        )
    ).scalar()
    rows = (
        await db.execute(
            text(
                """
                SELECT id, actor_email, action, resource_type, resource_id,
                       old_values, new_values, created_at
                FROM super_admin.audit_logs
                WHERE resource_type = 'organization' AND resource_id = :org_id
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"org_id": str(org_id), "limit": limit, "offset": offset},
        )
    ).mappings()
    return OrgAuditResponse(
        total=total or 0,
        items=[
            {
                "id": r["id"],
                "actor_email": r["actor_email"],
                "action": r["action"],
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "old_values": r["old_values"],
                "new_values": r["new_values"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    )


# ── User profile (Phase 3) ──────────────────────────────────────────────────


@router.get("/users/{user_id}", response_model=UserProfile)
async def get_user_profile(
    user_id: UUID,
    unmask: bool = Query(False),
    db: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("users.view")),
):
    row = (
        await db.execute(
            text(
                """
                SELECT u.id, u.org_id, o.name AS org_name, u.email, u.full_name,
                       u.is_active, u.created_at,
                       (SELECT count(*) FROM bookings b
                          JOIN time_slots t ON t.id = b.slot_id AND t.org_id = u.org_id
                         WHERE b.customer_email = u.email) AS booking_count,
                       (SELECT count(*) FROM chat_sessions s
                        WHERE s.org_id = u.org_id AND s.channel_user_id = u.email) AS conversation_count,
                       (SELECT max(s2.updated_at) FROM chat_sessions s2
                        WHERE s2.org_id = u.org_id AND s2.channel_user_id = u.email) AS last_activity_at
                FROM users u
                JOIN organizations o ON o.id = u.org_id
                WHERE u.id = :user_id
                """
            ),
            {"user_id": user_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    bookings = (
        await db.execute(
            text(
                """
                SELECT b.id, b.customer_name, b.customer_email, b.customer_phone,
                       b.org_slot_start_at, b.org_slot_end_at, b.meeting_link_url,
                       b.meeting_type_snapshot, b.created_at
                FROM bookings b
                JOIN time_slots t ON t.id = b.slot_id
                WHERE t.org_id = :org_id AND b.customer_email = :email
                ORDER BY b.created_at DESC
                LIMIT 10
                """
            ),
            {"org_id": row["org_id"], "email": row["email"]},
        )
    ).mappings()

    can_unmask = _can_unmask(admin, unmask)
    user = _masked_user_row(dict(row), can_unmask)
    return UserProfile(
        id=user["id"],
        org_id=user["org_id"],
        org_name=user["org_name"],
        email=user["email"],
        full_name=user["full_name"],
        is_active=user["is_active"],
        created_at=user["created_at"],
        booking_count=user["booking_count"] or 0,
        conversation_count=user["conversation_count"] or 0,
        last_activity_at=user["last_activity_at"],
        recent_bookings=_masked_booking_rows(bookings, can_unmask),
    )


@router.get("/users/{user_id}/activity", response_model=UserActivityResponse)
async def user_activity(
    user_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_readonly_db),
    _: dict = Depends(require_permission("users.view")),
):
    """Control-plane actions that touched this user (super_admin.audit_logs)."""
    rows = (
        await db.execute(
            text(
                """
                SELECT id, actor_email, action, resource_type, resource_id, created_at
                FROM super_admin.audit_logs
                WHERE resource_type = 'user' AND resource_id = :user_id
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                """
            ),
            {"user_id": str(user_id), "limit": limit},
        )
    ).mappings()
    return UserActivityResponse(
        items=[
            {
                "id": r["id"],
                "actor_email": r["actor_email"],
                "action": r["action"],
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    )
