"""
Dashboard API — all endpoints scoped to the current user's organization.
"""

import logging
import os
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Any, Dict
from urllib.parse import quote, unquote, urlsplit
from pydantic import BaseModel, Field, ValidationError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func as sa_func

from app.db.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.session import ChatSession
from app.models.booking import Booking, TimeSlot
from app.models.config import (
    OrgConfig,
    CalendarProviderType,
    MeetingMode,
    MeetingProviderDefault,
    ActiveMeetingProvider,
)
from app.models.message import ChatMessage
from app.models.system_alert import SystemAlert
from app.api.v1.endpoints.auth import get_current_user, deny_impersonation
from app.core.audit import log_audit_event
from app.schemas.audit import AuditAction
from app.core.mail import send_cancellation_email
from app.core.health_status import build_dependency_health_payload
from app.core.config import settings
from asgi_correlation_id.context import correlation_id
from app.schemas.agent import FieldDefinition, get_required_field_names, order_field_definitions, _sanitize_icon as sanitize_icon

router = APIRouter()
logger = logging.getLogger(__name__)
MAX_FIELD_DEFINITIONS = 20
MAX_SELECT_OPTIONS = 50
CORE_FIELD_NAMES = {"name", "email", "phone", "date", "time"}


def _allows_optional_contact_fields(custom_prompts: Any) -> bool:
    if not isinstance(custom_prompts, dict):
        return False
    value = custom_prompts.get("allow_optional_contact_fields")
    return value is True


def _enforce_contact_optionality_policy(
    required_fields: Optional[List[str]],
    custom_prompts: Any,
) -> None:
    if required_fields is None:
        return
    normalized = {str(field).strip().lower() for field in required_fields if str(field).strip()}
    missing_contacts = sorted({"email", "phone"} - normalized)
    if missing_contacts and not _allows_optional_contact_fields(custom_prompts):
        raise HTTPException(
            status_code=422,
            detail=(
                "Org policy does not permit optional email/phone fields. "
                f"Missing required contact fields: {', '.join(missing_contacts)}"
            ),
        )


def _normalize_field_definitions(field_definitions: Any) -> tuple[List[Dict[str, Any]], List[str]]:
    if not isinstance(field_definitions, list):
        raise HTTPException(
            status_code=422,
            detail="field_definitions must be a list",
        )
    if len(field_definitions) > MAX_FIELD_DEFINITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"field_definitions cannot exceed {MAX_FIELD_DEFINITIONS} fields",
        )

    normalized: List[FieldDefinition] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(field_definitions):
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=422,
                detail=f"field_definitions[{index}] must be an object",
            )
        try:
            definition = FieldDefinition(**raw)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid field_definitions[{index}]: {exc.errors()}",
            ) from exc

        definition.name = definition.name.strip().lower()
        if not definition.name:
            raise HTTPException(status_code=422, detail="field_definitions name is required")
        if definition.name in seen_names:
            raise HTTPException(
                status_code=422,
                detail=f"Duplicate field name: {definition.name}",
            )
        seen_names.add(definition.name)
        definition.icon = sanitize_icon(definition.icon, definition.name)

        if definition.type == "select":
            options = [str(option).strip() for option in definition.options if str(option).strip()]
            if not options:
                raise HTTPException(
                    status_code=422,
                    detail=f"Select field '{definition.name}' must include at least one value in options",
                )
            if len(options) > MAX_SELECT_OPTIONS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Select field '{definition.name}' cannot exceed {MAX_SELECT_OPTIONS} options",
                )
            definition.options = options

        normalized.append(definition)

    if not CORE_FIELD_NAMES.issubset({field.name for field in normalized}):
        raise HTTPException(
            status_code=422,
            detail="field_definitions must include core fields: name, email, phone, date, time",
        )

    ordered = order_field_definitions(normalized)
    required_fields = get_required_field_names(ordered)
    return [field.model_dump(mode="python") for field in ordered], required_fields


def _normalize_meeting_mode(mode: MeetingMode | str | None) -> MeetingMode:
    if isinstance(mode, MeetingMode):
        return mode
    if isinstance(mode, str):
        try:
            return MeetingMode(mode)
        except ValueError:
            return MeetingMode.OFFLINE_ONLY
    return MeetingMode.OFFLINE_ONLY


def _available_meeting_types(
    mode: MeetingMode | str, allow_hybrid_selection: bool
) -> list[str]:
    normalized_mode = _normalize_meeting_mode(mode)
    if normalized_mode == MeetingMode.ONLINE_ONLY:
        return ["ONLINE"]
    if normalized_mode == MeetingMode.OFFLINE_ONLY:
        return ["OFFLINE"]
    if allow_hybrid_selection:
        return ["ONLINE", "OFFLINE"]
    return ["OFFLINE"]


def _meeting_policy_payload(config: OrgConfig) -> dict:
    mode = _normalize_meeting_mode(
        getattr(config, "meeting_mode", MeetingMode.OFFLINE_ONLY)
    )
    allow_hybrid_selection = getattr(config, "allow_hybrid_selection", True)
    provider = getattr(config, "meeting_provider_default", MeetingProviderDefault.NONE)
    updated_at = getattr(config, "meeting_policy_updated_at", None)
    updated_by = getattr(config, "meeting_policy_updated_by", None)

    return {
        "meeting_mode": mode.value if isinstance(mode, MeetingMode) else str(mode),
        "allow_hybrid_selection": bool(allow_hybrid_selection),
        "meeting_provider_default": provider.value
        if isinstance(provider, MeetingProviderDefault)
        else str(provider),
        "available_meeting_types": _available_meeting_types(
            mode, bool(allow_hybrid_selection)
        ),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "updated_by": updated_by,
    }


def _integrations_redirect_url(frontend_url: Optional[str] = None, **params: str) -> str:
    from urllib.parse import urlencode

    base_url = (frontend_url or settings.FRONTEND_URL).rstrip("/")
    qs = urlencode(params)
    return (
        f"{base_url}/dashboard/integrations?{qs}"
        if qs
        else f"{base_url}/dashboard/integrations"
    )


def _callback_url(request: Request, route_name: str) -> str:
    return str(request.url_for(route_name))


def _runtime_frontend_url(request: Request) -> str:
    origin = (request.headers.get("origin") or "").strip()
    if origin.startswith("http://") or origin.startswith("https://"):
        return origin.rstrip("/")

    referer = (request.headers.get("referer") or "").strip()
    if referer.startswith("http://") or referer.startswith("https://"):
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    return settings.FRONTEND_URL.rstrip("/")


def _oauth_state_signature(payload: str) -> str:
    secret = (
        os.getenv("JWT_SECRET_KEY")
        or os.getenv("SECRET_KEY")
        or "dotstark-oauth-state"
    )
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _build_oauth_state(request: Request, org_id: Any, prefix: Optional[str] = None) -> str:
    base = f"{prefix}:{org_id}" if prefix else str(org_id)
    encoded_frontend = quote(_runtime_frontend_url(request), safe="")
    payload = f"{base}|ret={encoded_frontend}"
    sig = _oauth_state_signature(payload)
    return f"{payload}|sig={sig}"


def _parse_oauth_state(state: str) -> tuple[str, Optional[str], Optional[str]]:
    raw_state = (state or "").strip()
    parts = raw_state.split("|") if raw_state else [""]
    base = parts[0]
    ret_part: Optional[str] = None
    sig_part: Optional[str] = None
    for part in parts[1:]:
        if part.startswith("ret="):
            ret_part = part[4:]
        elif part.startswith("sig="):
            sig_part = part[4:]

    runtime_frontend: Optional[str] = None
    if ret_part and sig_part:
        payload = f"{base}|ret={ret_part}"
        expected = _oauth_state_signature(payload)
        if hmac.compare_digest(sig_part, expected):
            decoded = unquote(ret_part).strip()
            if decoded.startswith("http://") or decoded.startswith("https://"):
                runtime_frontend = decoded.rstrip("/")
        else:
            logger.warning("Ignoring OAuth state runtime frontend due to signature mismatch")

    state_prefix: Optional[str] = None
    org_id = base
    if ":" in base:
        maybe_prefix, maybe_org_id = base.split(":", 1)
        if maybe_prefix in {"meet", "teams", "zoom"}:
            state_prefix = maybe_prefix
            org_id = maybe_org_id

    return org_id, state_prefix, runtime_frontend


def _read_env_redirect_uri(name: str, fallback: str) -> str:
    value = (os.getenv(name) or "").strip()
    return value or fallback


def _oauth_runtime_warnings(frontend_url: str, redirect_uris: Dict[str, str]) -> List[str]:
    warnings: List[str] = []
    combined = [frontend_url, *redirect_uris.values()]
    if any("localhost" in url or "127.0.0.1" in url for url in combined):
        warnings.append(
            "One or more OAuth/public URLs still point to localhost. External Cloudflare users will fail redirects until updated."
        )
    if any("trycloudflare.com" in url for url in combined):
        warnings.append(
            "trycloudflare URLs rotate on restart. OAuth providers require stable redirect URIs; use a stable tunnel/domain before production OAuth testing."
        )
    return warnings


class RuntimeUrlsResponse(BaseModel):
    frontend_url: str
    widget_script_url: str
    public_api_base: str
    oauth_redirect_uris: Dict[str, str]
    warnings: List[str]


@router.get("/runtime-urls", response_model=RuntimeUrlsResponse)
async def get_runtime_urls(
    user: User = Depends(get_current_user),
) -> RuntimeUrlsResponse:
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    google_redirect = _read_env_redirect_uri(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8005/api/v1/dashboard/google-calendar/callback",
    )
    microsoft_redirect = _read_env_redirect_uri(
        "MICROSOFT_REDIRECT_URI",
        "http://localhost:8005/api/v1/dashboard/outlook-calendar/callback",
    )
    zoom_redirect = _read_env_redirect_uri(
        "ZOOM_REDIRECT_URI",
        "http://localhost:8005/api/v1/dashboard/meeting-providers/zoom/callback",
    )
    redirect_uris = {
        "google": google_redirect,
        "microsoft": microsoft_redirect,
        "zoom": zoom_redirect,
    }

    public_api_base = (
        os.getenv("PUBLIC_API_BASE")
        or os.getenv("NEXT_PUBLIC_API_BASE")
        or "/api/v1"
    ).rstrip("/")
    if not public_api_base:
        public_api_base = "/api/v1"
    if public_api_base.endswith("/api/v1"):
        widget_script_url = f"{public_api_base[:-7].rstrip('/')}/static/widget/widget.js"
    else:
        widget_script_url = f"{public_api_base.rstrip('/')}/static/widget/widget.js"

    warnings = _oauth_runtime_warnings(frontend_url, redirect_uris)

    return RuntimeUrlsResponse(
        frontend_url=frontend_url,
        widget_script_url=widget_script_url,
        public_api_base=public_api_base,
        oauth_redirect_uris=redirect_uris,
        warnings=warnings,
    )


# ══════════════════════════════════════════════════
#  RESPONSE SCHEMAS
# ══════════════════════════════════════════════════


class StatsResponse(BaseModel):
    total_bookings: int
    active_sessions: int
    confirmed_sessions: int
    cancelled_sessions: int
    has_schedule: bool  # True if a BusinessSchedule is configured
    bookings_this_week: int  # Confirmed bookings in last 7 days
    unresolved_alerts_total: int
    unresolved_critical_alerts: int
    reliability_link: str


class BookingItem(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    customer_phone: Optional[str]
    slot_start: Optional[datetime]
    slot_end: Optional[datetime]
    created_at: datetime


class ConversationItem(BaseModel):
    id: str
    session_status: str
    current_intent: Optional[str]
    collected_data: dict
    missing_fields: list
    created_at: datetime
    updated_at: datetime
    channel: str = "web"


class AgentConfigResponse(BaseModel):
    required_fields: list
    field_definitions: Optional[list] = None
    custom_prompts: Optional[dict] = None
    timezone: str
    ui_brand_color: str
    org_name: str
    business_name: Optional[str] = None
    api_key: str
    require_otp: bool = True
    max_booking_days_ahead: int = 90
    session_retention_days: int = 30
    session_cleanup_frequency: str = "daily"
    session_cleanup_hour_utc: int = 0
    meeting_policy: dict
    # Website crawl state — surfaced so the admin UI can restore in-progress
    # crawl context after a page refresh.
    website_url: Optional[str] = None
    crawl_id: Optional[str] = None
    crawl_status: Optional[str] = None
    crawl_started_at: Optional[str] = None
    crawl_completed_at: Optional[str] = None
    total_pages_crawled: Optional[int] = None


class AgentConfigUpdate(BaseModel):
    required_fields: Optional[list] = None
    field_definitions: Optional[list] = None
    custom_prompts: Optional[dict] = None
    timezone: Optional[str] = None
    ui_brand_color: Optional[str] = None
    org_name: Optional[str] = None
    business_name: Optional[str] = None
    require_otp: Optional[bool] = None
    max_booking_days_ahead: Optional[int] = None
    session_retention_days: Optional[int] = Field(default=None, ge=7, le=365)
    session_cleanup_frequency: Optional[str] = None
    session_cleanup_hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    meeting_mode: Optional[MeetingMode] = None
    allow_hybrid_selection: Optional[bool] = None
    meeting_provider_default: Optional[MeetingProviderDefault] = None


class SlotItem(BaseModel):
    id: str
    start_time: datetime
    end_time: datetime
    is_booked: bool


class SlotCreate(BaseModel):
    start_time: datetime
    end_time: datetime


class CancelBookingResponse(BaseModel):
    success: bool
    booking_id: str
    message: str


class DependencyHealthCheck(BaseModel):
    status: str
    latency_ms: Optional[float]
    error: Optional[str]


class DependencyHealthResponse(BaseModel):
    status: str
    request_id: Optional[str]
    trace_id: Optional[str]
    checks: dict


# ══════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Overview statistics for the current org."""
    org_id = user.org_id

    # Total bookings
    total_bookings_sq = (
        select(sa_func.count())
        .select_from(Booking)
        .where(Booking.org_id == org_id)
        .scalar_subquery()
    )

    active_sessions_sq = (
        select(sa_func.count())
        .select_from(ChatSession)
        .where(ChatSession.org_id == org_id, ChatSession.session_status == "ACTIVE")
        .scalar_subquery()
    )
    confirmed_sessions_sq = (
        select(sa_func.count())
        .select_from(ChatSession)
        .where(ChatSession.org_id == org_id, ChatSession.session_status == "CONFIRMED")
        .scalar_subquery()
    )
    cancelled_sessions_sq = (
        select(sa_func.count())
        .select_from(ChatSession)
        .where(ChatSession.org_id == org_id, ChatSession.session_status == "CANCELLED")
        .scalar_subquery()
    )

    # Schedule configured?
    from app.models.booking import BusinessSchedule

    has_schedule_sq = (
        select(sa_func.count())
        .select_from(BusinessSchedule)
        .where(BusinessSchedule.org_id == org_id)
        .scalar_subquery()
    )

    # Bookings this week
    from datetime import timedelta

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    bookings_week_sq = (
        select(sa_func.count())
        .select_from(Booking)
        .where(Booking.org_id == org_id, Booking.created_at >= week_ago)
        .scalar_subquery()
    )
    unresolved_alerts_total_sq = (
        select(sa_func.count())
        .select_from(SystemAlert)
        .where(
            (SystemAlert.org_id == org_id) | (SystemAlert.org_id.is_(None)),
            SystemAlert.resolved_at.is_(None),
        )
        .scalar_subquery()
    )
    unresolved_critical_alerts_sq = (
        select(sa_func.count())
        .select_from(SystemAlert)
        .where(
            (SystemAlert.org_id == org_id) | (SystemAlert.org_id.is_(None)),
            SystemAlert.resolved_at.is_(None),
            SystemAlert.severity == "critical",
        )
        .scalar_subquery()
    )

    stats_result = await db.execute(
        select(
            total_bookings_sq.label("total_bookings"),
            active_sessions_sq.label("active_sessions"),
            confirmed_sessions_sq.label("confirmed_sessions"),
            cancelled_sessions_sq.label("cancelled_sessions"),
            has_schedule_sq.label("has_schedule_count"),
            bookings_week_sq.label("bookings_this_week"),
            unresolved_alerts_total_sq.label("unresolved_alerts_total"),
            unresolved_critical_alerts_sq.label("unresolved_critical_alerts"),
        )
    )
    stats = stats_result.one()

    return StatsResponse(
        total_bookings=stats.total_bookings or 0,
        active_sessions=stats.active_sessions or 0,
        confirmed_sessions=stats.confirmed_sessions or 0,
        cancelled_sessions=stats.cancelled_sessions or 0,
        has_schedule=(stats.has_schedule_count or 0) > 0,
        bookings_this_week=stats.bookings_this_week or 0,
        unresolved_alerts_total=stats.unresolved_alerts_total or 0,
        unresolved_critical_alerts=stats.unresolved_critical_alerts or 0,
        reliability_link="/dashboard/reliability?unresolved_only=true&severity=critical&recent_hours=24",
    )


@router.get("/dependency-health", response_model=DependencyHealthResponse)
async def get_dependency_health(
    request: Request,
    user: User = Depends(get_current_user),
):
    _ = user
    request_id = correlation_id.get() or request.headers.get("x-request-id")
    trace_id = request.headers.get("x-trace-id")
    return await build_dependency_health_payload(
        request_id=request_id, trace_id=trace_id
    )


# Calendar view - primary schema for calendar events
class CalendarEventItem(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    customer_phone: Optional[str] = None
    slot_start: Optional[datetime] = None
    slot_end: Optional[datetime] = None
    created_at: datetime
    collected_data: dict = {}  # full state_data from ChatSession


@router.get("/recent-bookings", response_model=List[BookingItem])
async def get_recent_bookings(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent bookings for the current org (JOIN eliminates N+1)."""
    from sqlalchemy.orm import selectinload

    # Single query with JOIN instead of N+1 loop
    result = await db.execute(
        select(Booking, TimeSlot)
        .outerjoin(TimeSlot, Booking.slot_id == TimeSlot.id)
        .where(Booking.org_id == user.org_id)
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    rows = result.all()

    items = []
    for b, slot in rows:
        items.append(
            BookingItem(
                id=str(b.id),
                customer_name=b.customer_name or "Unknown",
                customer_email=b.customer_email or "",
                customer_phone=b.customer_phone,
                slot_start=slot.start_time if slot else None,
                slot_end=slot.end_time if slot else None,
                created_at=b.created_at,
            )
        )
    return items


@router.get("/conversations", response_model=List[ConversationItem])
async def get_conversations(
    limit: int = Query(default=200, ge=1, le=500),
    include_archived: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List chat sessions for the current org."""
    predicates = [ChatSession.org_id == user.org_id]
    if not include_archived:
        predicates.append(ChatSession.session_status != "ARCHIVED")
        predicates.append(ChatSession.archived_at.is_(None))

    result = await db.execute(
        select(ChatSession)
        .where(*predicates)
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )
    sessions = result.scalars().all()

    return [
        ConversationItem(
            id=str(s.id),
            session_status=s.session_status or "ACTIVE",
            current_intent=s.current_intent,
            collected_data=s.state_data or {},
            missing_fields=s.missing_fields or [],
            created_at=s.created_at,
            updated_at=s.updated_at,
            channel=s.channel or "web",
        )
        for s in sessions
    ]


@router.get("/agent-config", response_model=AgentConfigResponse)
async def get_agent_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the agent configuration for the current org."""
    from sqlalchemy.exc import IntegrityError

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()
    if not config:
        config = OrgConfig(
            org_id=user.org_id,
            required_fields=["name", "email", "phone", "date", "time"],
            field_definitions=[],
            custom_prompts={},
            timezone="UTC",
            ui_brand_color="#3B82F6",
            require_otp=True,
            max_booking_days_ahead=90,
            session_retention_days=30,
            session_cleanup_frequency="daily",
            session_cleanup_hour_utc=0,
            meeting_mode=MeetingMode.OFFLINE_ONLY,
            allow_hybrid_selection=True,
            meeting_provider_default=MeetingProviderDefault.NONE,
        )
        db.add(config)
        try:
            await db.commit()
            await db.refresh(config)
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(OrgConfig).where(OrgConfig.org_id == user.org_id)
            )
            config = result.scalars().first()

    org_result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = org_result.scalars().first()

    masked_api_key = ""
    if org and org.api_key:
        masked_api_key = f"{org.api_key[:4]}...{org.api_key[-4:]}"

    return AgentConfigResponse(
        required_fields=config.required_fields,
        field_definitions=config.field_definitions,
        custom_prompts=config.custom_prompts,
        timezone=config.timezone or "UTC",
        ui_brand_color=config.ui_brand_color or "#3B82F6",
        org_name=config.org_name if getattr(config, "org_name", None) else (org.name if org else "Unknown"),
        business_name=getattr(config, "business_name", None),
        api_key=masked_api_key,
        require_otp=config.require_otp if hasattr(config, "require_otp") else True,
        max_booking_days_ahead=config.max_booking_days_ahead
        if hasattr(config, "max_booking_days_ahead")
        else 90,
        session_retention_days=config.session_retention_days
        if hasattr(config, "session_retention_days")
        else 30,
        session_cleanup_frequency=config.session_cleanup_frequency
        if hasattr(config, "session_cleanup_frequency")
        else "daily",
        session_cleanup_hour_utc=config.session_cleanup_hour_utc
        if hasattr(config, "session_cleanup_hour_utc")
        else 0,
        meeting_policy=_meeting_policy_payload(config),
        website_url=getattr(config, "website_url", None),
        crawl_id=getattr(config, "crawl_id", None),
        crawl_status=getattr(config, "crawl_status", None),
        crawl_started_at=(
            config.crawl_started_at.isoformat()
            if getattr(config, "crawl_started_at", None)
            else None
        ),
        crawl_completed_at=(
            config.crawl_completed_at.isoformat()
            if getattr(config, "crawl_completed_at", None)
            else None
        ),
        total_pages_crawled=getattr(config, "total_pages_crawled", None),
    )


@router.put("/agent-config", response_model=AgentConfigResponse)
async def update_agent_config(
    update: AgentConfigUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update agent configuration."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="Agent configuration not found")

    old_values = {
        "required_fields": config.required_fields,
        "field_definitions": config.field_definitions,
        "custom_prompts": config.custom_prompts,
        "timezone": config.timezone,
        "ui_brand_color": config.ui_brand_color,
        "require_otp": config.require_otp if hasattr(config, "require_otp") else None,
        "max_booking_days_ahead": config.max_booking_days_ahead
        if hasattr(config, "max_booking_days_ahead")
        else None,
        "session_retention_days": config.session_retention_days
        if hasattr(config, "session_retention_days")
        else None,
        "session_cleanup_frequency": config.session_cleanup_frequency
        if hasattr(config, "session_cleanup_frequency")
        else None,
        "session_cleanup_hour_utc": config.session_cleanup_hour_utc
        if hasattr(config, "session_cleanup_hour_utc")
        else None,
        "meeting_mode": config.meeting_mode.value
        if hasattr(config, "meeting_mode") and config.meeting_mode
        else "OFFLINE_ONLY",
        "allow_hybrid_selection": config.allow_hybrid_selection
        if hasattr(config, "allow_hybrid_selection")
        else True,
        "meeting_provider_default": config.meeting_provider_default.value
        if hasattr(config, "meeting_provider_default")
        and config.meeting_provider_default
        else "NONE",
        "meeting_policy_updated_at": config.meeting_policy_updated_at.isoformat()
        if hasattr(config, "meeting_policy_updated_at")
        and config.meeting_policy_updated_at
        else None,
        "meeting_policy_updated_by": config.meeting_policy_updated_by
        if hasattr(config, "meeting_policy_updated_by")
        else None,
    }
    effective_custom_prompts = (
        update.custom_prompts if update.custom_prompts is not None else config.custom_prompts
    )

    if update.required_fields is not None:
        _enforce_contact_optionality_policy(update.required_fields, effective_custom_prompts)
        config.required_fields = update.required_fields
    if update.field_definitions is not None:
        normalized_defs, normalized_required_fields = _normalize_field_definitions(
            update.field_definitions
        )
        _enforce_contact_optionality_policy(
            normalized_required_fields,
            effective_custom_prompts,
        )
        config.field_definitions = normalized_defs
        config.required_fields = normalized_required_fields
    if update.custom_prompts is not None:
        config.custom_prompts = update.custom_prompts
    if update.timezone is not None:
        config.timezone = update.timezone
    if update.ui_brand_color is not None:
        config.ui_brand_color = update.ui_brand_color
    if update.require_otp is not None:
        config.require_otp = update.require_otp
    if update.max_booking_days_ahead is not None:
        config.max_booking_days_ahead = update.max_booking_days_ahead
    if update.session_retention_days is not None:
        config.session_retention_days = update.session_retention_days
    if update.session_cleanup_frequency is not None:
        config.session_cleanup_frequency = update.session_cleanup_frequency
    if update.session_cleanup_hour_utc is not None:
        config.session_cleanup_hour_utc = update.session_cleanup_hour_utc
    if update.meeting_mode is not None:
        config.meeting_mode = update.meeting_mode
        config.meeting_policy_updated_at = datetime.now(timezone.utc)
        config.meeting_policy_updated_by = user.email
    if update.allow_hybrid_selection is not None:
        config.allow_hybrid_selection = update.allow_hybrid_selection
        config.meeting_policy_updated_at = datetime.now(timezone.utc)
        config.meeting_policy_updated_by = user.email
    if update.meeting_provider_default is not None:
        config.meeting_provider_default = update.meeting_provider_default
        config.meeting_policy_updated_at = datetime.now(timezone.utc)
        config.meeting_policy_updated_by = user.email
    if update.org_name is not None:
        config.org_name = update.org_name
    if update.business_name is not None:
        config.business_name = update.business_name

    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(config, "required_fields")
    if config.field_definitions is not None:
        flag_modified(config, "field_definitions")
    if config.custom_prompts is not None:
        flag_modified(config, "custom_prompts")

    await db.commit()
    await db.refresh(config)

    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.CONFIG_UPDATE,
        resource_type="org_config",
        resource_id=str(config.org_id),
        old_values=old_values,
        new_values={
            "required_fields": config.required_fields,
            "field_definitions": config.field_definitions,
            "custom_prompts": config.custom_prompts,
            "timezone": config.timezone,
            "ui_brand_color": config.ui_brand_color,
            "require_otp": config.require_otp
            if hasattr(config, "require_otp")
            else None,
            "max_booking_days_ahead": config.max_booking_days_ahead
            if hasattr(config, "max_booking_days_ahead")
            else None,
            "session_retention_days": config.session_retention_days
            if hasattr(config, "session_retention_days")
            else None,
            "session_cleanup_frequency": config.session_cleanup_frequency
            if hasattr(config, "session_cleanup_frequency")
            else None,
            "session_cleanup_hour_utc": config.session_cleanup_hour_utc
            if hasattr(config, "session_cleanup_hour_utc")
            else None,
            "meeting_mode": config.meeting_mode.value
            if hasattr(config, "meeting_mode") and config.meeting_mode
            else "OFFLINE_ONLY",
            "allow_hybrid_selection": config.allow_hybrid_selection
            if hasattr(config, "allow_hybrid_selection")
            else True,
            "meeting_provider_default": config.meeting_provider_default.value
            if hasattr(config, "meeting_provider_default")
            and config.meeting_provider_default
            else "NONE",
            "meeting_policy_updated_at": config.meeting_policy_updated_at.isoformat()
            if hasattr(config, "meeting_policy_updated_at")
            and config.meeting_policy_updated_at
            else None,
            "meeting_policy_updated_by": config.meeting_policy_updated_by
            if hasattr(config, "meeting_policy_updated_by")
            else None,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    org_result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = org_result.scalars().first()
    masked_api_key = ""
    if org and org.api_key:
        masked_api_key = f"{org.api_key[:4]}...{org.api_key[-4:]}"

    return AgentConfigResponse(
        required_fields=config.required_fields,
        field_definitions=config.field_definitions,
        custom_prompts=config.custom_prompts,
        timezone=config.timezone or "UTC",
        ui_brand_color=config.ui_brand_color or "#3B82F6",
        org_name=org.name if org else "Unknown",
        api_key=masked_api_key,
        require_otp=config.require_otp if hasattr(config, "require_otp") else True,
        max_booking_days_ahead=config.max_booking_days_ahead
        if hasattr(config, "max_booking_days_ahead")
        else 90,
        session_retention_days=config.session_retention_days
        if hasattr(config, "session_retention_days")
        else 30,
        session_cleanup_frequency=config.session_cleanup_frequency
        if hasattr(config, "session_cleanup_frequency")
        else "daily",
        session_cleanup_hour_utc=config.session_cleanup_hour_utc
        if hasattr(config, "session_cleanup_hour_utc")
        else 0,
        meeting_policy=_meeting_policy_payload(config),
    )


@router.get("/availability", response_model=List[SlotItem])
async def get_availability(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all time slots for the current org."""
    result = await db.execute(
        select(TimeSlot)
        .where(TimeSlot.org_id == user.org_id)
        .order_by(TimeSlot.start_time)
    )
    slots = result.scalars().all()
    return [
        SlotItem(
            id=str(s.id),
            start_time=s.start_time,
            end_time=s.end_time,
            is_booked=s.is_booked,
        )
        for s in slots
    ]


@router.post("/availability", response_model=SlotItem, status_code=201)
async def create_availability(
    slot_data: SlotCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new time slot."""
    slot = TimeSlot(
        org_id=user.org_id,
        start_time=slot_data.start_time,
        end_time=slot_data.end_time,
        is_booked=False,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.BOOKING_CREATE,
        resource_type="time_slot",
        resource_id=str(slot.id),
        old_values=None,
        new_values={
            "start_time": slot.start_time.isoformat() if slot.start_time else None,
            "end_time": slot.end_time.isoformat() if slot.end_time else None,
            "is_booked": slot.is_booked,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return SlotItem(
        id=str(slot.id),
        start_time=slot.start_time,
        end_time=slot.end_time,
        is_booked=slot.is_booked,
    )


@router.delete("/availability/{slot_id}", status_code=204)
async def delete_availability(
    slot_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a time slot."""
    result = await db.execute(
        select(TimeSlot).where(TimeSlot.id == slot_id, TimeSlot.org_id == user.org_id)
    )
    slot = result.scalars().first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    old_values = {
        "start_time": slot.start_time.isoformat() if slot.start_time else None,
        "end_time": slot.end_time.isoformat() if slot.end_time else None,
        "is_booked": slot.is_booked,
    }
    await db.delete(slot)
    await db.commit()
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.BOOKING_DELETE,
        resource_type="time_slot",
        resource_id=slot_id,
        old_values=old_values,
        new_values=None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/bookings/{booking_id}/cancel", response_model=CancelBookingResponse)
async def cancel_booking_admin(
    booking_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an existing booking for the authenticated org."""
    from app.crud.booking import cancel_booking

    booking_result = await db.execute(
        select(Booking).where(Booking.id == booking_id, Booking.org_id == user.org_id)
    )
    booking = booking_result.scalars().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == booking.session_id, ChatSession.org_id == user.org_id
        )
    )
    chat_session = session_result.scalars().first()

    old_values = {
        "session_status": chat_session.session_status if chat_session else None,
        "date": (chat_session.state_data or {}).get("date") if chat_session else None,
        "time": (chat_session.state_data or {}).get("time") if chat_session else None,
    }

    try:
        cancelled = await cancel_booking(
            db,
            booking_id=booking_id,
            org_id=user.org_id,
        )
    except ValueError as conflict:
        raise HTTPException(status_code=409, detail=str(conflict))

    if not cancelled:
        raise HTTPException(status_code=404, detail="Booking not found")

    if chat_session:
        chat_session.session_status = "CANCELLED"
        await db.commit()

    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.BOOKING_CANCEL,
        resource_type="booking",
        resource_id=booking_id,
        old_values=old_values,
        new_values={
            "session_status": "CANCELLED",
            "date": old_values.get("date"),
            "time": old_values.get("time"),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    if chat_session:
        try:
            customer_email = (chat_session.state_data or {}).get(
                "email"
            ) or booking.customer_email
            if customer_email:
                booking_details = {
                    "name": (chat_session.state_data or {}).get("name")
                    or booking.customer_name,
                    "email": customer_email,
                    "date": (chat_session.state_data or {}).get("date"),
                    "time": (chat_session.state_data or {}).get("time"),
                }

                if booking.meeting_type_snapshot:
                    booking_details["meeting_type_snapshot"] = (
                        booking.meeting_type_snapshot
                    )
                if booking.meeting_link_status:
                    booking_details["meeting_link_status"] = (
                        booking.meeting_link_status.value
                    )
                if booking.meeting_link_url:
                    booking_details["meeting_link_url"] = booking.meeting_link_url
                if booking.meeting_link_provider:
                    booking_details["meeting_link_provider"] = (
                        booking.meeting_link_provider.value
                    )
                if booking.user_timezone:
                    booking_details["user_timezone"] = booking.user_timezone
                if booking.org_timezone_snapshot:
                    booking_details["org_timezone_snapshot"] = (
                        booking.org_timezone_snapshot
                    )

                await send_cancellation_email(
                    customer_email=customer_email,
                    customer_name=(chat_session.state_data or {}).get("name")
                    or booking.customer_name
                    or "there",
                    booking_details=booking_details,
                    org_id=user.org_id,
                )
        except Exception as mail_error:
            logger.warning(f"Admin cancellation email failed: {mail_error}")

    return CancelBookingResponse(
        success=True,
        booking_id=booking_id,
        message="Booking cancelled and slot released.",
    )


# ══════════════════════════════════════════════════
#  ONBOARDING STATUS
# ══════════════════════════════════════════════════


class OnboardingStep(BaseModel):
    key: str
    label: str
    description: str
    completed: bool
    link: str


class OnboardingStatusResponse(BaseModel):
    steps: List[OnboardingStep]
    completed_count: int
    total_count: int


@router.get("/onboarding-status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check what onboarding steps the tenant has completed."""
    org_id = user.org_id

    # Check OrgConfig exists and has custom fields
    config_result = await db.execute(
        select(OrgConfig).where(OrgConfig.org_id == org_id)
    )
    config = config_result.scalars().first()

    has_persona = bool(
        config
        and isinstance(config.custom_prompts, dict)
        and config.custom_prompts.get("agent_name")
    )

    has_fields = bool(
        config and config.required_fields and len(config.required_fields) > 0
    )

    # Check if a BusinessSchedule is configured
    from app.models.booking import BusinessSchedule

    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org_id)
    )
    has_schedule = schedule_result.scalars().first() is not None

    # Check if at least one booking exists
    booking_count = await db.execute(
        select(sa_func.count(Booking.id)).where(Booking.org_id == org_id)
    )
    has_bookings = (booking_count.scalar() or 0) > 0

    steps = [
        OnboardingStep(
            key="configure_agent",
            label="Configure Your Agent",
            description="Set your agent's name, greeting message, and personality.",
            completed=has_persona,
            link="/dashboard/agent",
        ),
        OnboardingStep(
            key="define_fields",
            label="Define Booking Fields",
            description="Choose what information your agent collects from customers.",
            completed=has_fields,
            link="/dashboard/agent",
        ),
        OnboardingStep(
            key="add_availability",
            label="Configure Business Hours",
            description="Set your weekly schedule — days, hours, and breaks.",
            completed=has_schedule,
            link="/dashboard/business-hours",
        ),
        OnboardingStep(
            key="first_booking",
            label="Get Your First Booking",
            description="Deploy the widget and receive your first real booking!",
            completed=has_bookings,
            link="/dashboard/integrations",
        ),
    ]

    return OnboardingStatusResponse(
        steps=steps,
        completed_count=sum(1 for s in steps if s.completed),
        total_count=len(steps),
    )


# ══════════════════════════════════════════════════
#  CONVERSATION TRANSCRIPT
# ══════════════════════════════════════════════════


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    metadata: Optional[dict] = None
    created_at: datetime


@router.get("/conversations/{session_id}/messages", response_model=List[MessageItem])
async def get_conversation_messages(
    session_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the full message transcript for a chat session."""
    # Verify session belongs to the user's org
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.org_id == user.org_id,
        )
    )
    session = session_result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    messages = result.scalars().all()

    return [
        MessageItem(
            id=str(m.id),
            role=m.role,
            content=m.content,
            metadata=m.metadata_,
            created_at=m.created_at,
        )
        for m in messages
    ]


# ── WhatsApp Integration Config ──────────────────────────────


class WhatsAppConfigResponse(BaseModel):
    whatsapp_enabled: bool = False
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_business_account_id: Optional[str] = None
    whatsapp_verify_token: Optional[str] = None
    whatsapp_api_token_set: bool = False
    webhook_url: str = ""


class WhatsAppConfigUpdate(BaseModel):
    whatsapp_enabled: Optional[bool] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_business_account_id: Optional[str] = None
    whatsapp_verify_token: Optional[str] = None
    whatsapp_api_token: Optional[str] = None


@router.get("/whatsapp-config", response_model=WhatsAppConfigResponse)
async def get_whatsapp_config(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get WhatsApp integration configuration."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    base_url = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}/api/v1/whatsapp/webhook"

    return WhatsAppConfigResponse(
        whatsapp_enabled=config.whatsapp_enabled or False,
        whatsapp_phone_number_id=config.whatsapp_phone_number_id,
        whatsapp_business_account_id=config.whatsapp_business_account_id,
        whatsapp_verify_token=config.whatsapp_verify_token,
        whatsapp_api_token_set=config.whatsapp_api_token is not None,
        webhook_url=webhook_url,
    )


@router.put("/whatsapp-config", response_model=WhatsAppConfigResponse)
async def update_whatsapp_config(
    request: Request,
    update: WhatsAppConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update WhatsApp integration configuration."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    if update.whatsapp_enabled is not None:
        config.whatsapp_enabled = update.whatsapp_enabled
    if update.whatsapp_phone_number_id is not None:
        config.whatsapp_phone_number_id = update.whatsapp_phone_number_id
    if update.whatsapp_business_account_id is not None:
        config.whatsapp_business_account_id = update.whatsapp_business_account_id
    if update.whatsapp_verify_token is not None:
        config.whatsapp_verify_token = update.whatsapp_verify_token
    if update.whatsapp_api_token is not None:
        config.whatsapp_api_token = update.whatsapp_api_token

    await db.commit()
    await db.refresh(config)

    base_url = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}/api/v1/whatsapp/webhook"

    return WhatsAppConfigResponse(
        whatsapp_enabled=config.whatsapp_enabled or False,
        whatsapp_phone_number_id=config.whatsapp_phone_number_id,
        whatsapp_business_account_id=config.whatsapp_business_account_id,
        whatsapp_verify_token=config.whatsapp_verify_token,
        whatsapp_api_token_set=config.whatsapp_api_token is not None,
        webhook_url=webhook_url,
    )


# ══════════════════════════════════════════════════
#  GOOGLE CALENDAR INTEGRATION
# ══════════════════════════════════════════════════


class GoogleCalendarConfigResponse(BaseModel):
    google_calendar_enabled: bool = False
    google_calendar_connected: bool = False


class GoogleCalendarEventResponse(BaseModel):
    id: str
    summary: str
    description: Optional[str] = None
    start: str
    end: str


@router.get("/google-calendar/config", response_model=GoogleCalendarConfigResponse)
async def get_google_calendar_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get Google Calendar integration status."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        return GoogleCalendarConfigResponse()

    return GoogleCalendarConfigResponse(
        google_calendar_enabled=config.google_calendar_enabled or False,
        google_calendar_connected=config.google_calendar_connected or False,
    )


@router.get("/google-calendar/events", response_model=List[GoogleCalendarEventResponse])
async def get_google_calendar_events(
    start_date: Optional[str] = Query(
        default=None, description="Start date (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = Query(default=None, description="End date (YYYY-MM-DD)"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch events from Google Calendar."""
    from datetime import datetime as dt, timedelta
    from app.core.integrations.google_calendar import (
        get_calendar_events,
        refresh_access_token,
    )

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if (
        not config
        or not config.google_calendar_connected
        or not config.google_calendar_refresh_token
    ):
        return []

    # For now, return empty list if not configured with real credentials
    # In production, this would use the refresh token to get events
    return []


@router.post("/google-calendar/connect")
async def connect_google_calendar(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate Google Calendar OAuth flow.
    Returns the authorization URL that user should visit.
    """
    from app.core.integrations.google_calendar import get_google_auth_url

    try:
        auth_url = get_google_auth_url(
            state=_build_oauth_state(request, user.org_id),
            redirect_uri=_callback_url(request, "google_calendar_callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"auth_url": auth_url}


@router.post("/google-calendar/disconnect")
async def disconnect_google_calendar(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Google Calendar integration."""
    from app.core.integrations.calendar_providers.migration_task import (
        cleanup_old_provider_events,
    )

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if config:
        if config.google_calendar_connected:
            deleted_count = await cleanup_old_provider_events(
                db, config, CalendarProviderType.GOOGLE
            )
            logger.info(
                f"Cleaned up {deleted_count} Google events for org {user.org_id}"
            )

        config.google_calendar_connected = False
        config.google_calendar_enabled = False
        config.google_calendar_refresh_token = None
        if config.active_calendar_provider == CalendarProviderType.GOOGLE:
            config.active_calendar_provider = CalendarProviderType.NONE
        await db.commit()

    return {"status": "disconnected"}


# ══════════════════════════════════════════════════
#  UNIFIED CALENDAR CONFIG (Google + Outlook)
# ══════════════════════════════════════════════════


class CalendarConfigResponse(BaseModel):
    google_calendar_connected: bool = False
    outlook_calendar_connected: bool = False
    active_calendar_provider: Optional[str] = None


class SetActiveProviderRequest(BaseModel):
    provider: str  # "NONE", "GOOGLE", or "OUTLOOK"


async def _run_migration_task(
    db_session_factory,
    org_id: str,
    old_provider: str,
    new_provider: str,
    org_name: str,
):
    """Background task for migration - runs async without blocking."""
    from app.core.integrations.calendar_providers.migration_task import (
        migrate_bookings_to_new_provider,
    )
    from app.models.config import CalendarProviderType
    from app.models.organization import Organization

    async with db_session_factory() as db:
        result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        config = result.scalars().first()
        org_result = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = org_result.scalars().first()
        org_name_val = org.name if org else "Booking"

        if config and new_provider != "NONE":
            old_type = (
                CalendarProviderType(old_provider)
                if old_provider != "NONE"
                else CalendarProviderType.NONE
            )
            new_type = CalendarProviderType(new_provider)
            await migrate_bookings_to_new_provider(
                db, config, old_type, new_type, org_name_val
            )


@router.get("/calendar/config", response_model=CalendarConfigResponse)
async def get_calendar_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get calendar integration status for both Google and Outlook."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        return CalendarConfigResponse()

    active_provider = None
    if config.active_calendar_provider:
        active_provider = (
            config.active_calendar_provider.value
            if hasattr(config.active_calendar_provider, "value")
            else str(config.active_calendar_provider)
        )

    return CalendarConfigResponse(
        google_calendar_connected=config.google_calendar_connected or False,
        outlook_calendar_connected=config.outlook_calendar_connected or False,
        active_calendar_provider=active_provider,
    )


@router.put("/calendar/active-provider")
async def set_active_calendar_provider(
    request: SetActiveProviderRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Set the active calendar provider.
    If switching providers, triggers background migration of future bookings.
    """
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    old_provider = (
        config.active_calendar_provider.value
        if config.active_calendar_provider
        else "NONE"
        if hasattr(config.active_calendar_provider, "value")
        else str(config.active_calendar_provider or "NONE")
    )

    try:
        new_provider_type = CalendarProviderType(request.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider value")

    if (
        new_provider_type == CalendarProviderType.GOOGLE
        and not config.google_calendar_connected
    ):
        raise HTTPException(
            status_code=400,
            detail="Google Calendar must be connected before activation",
        )
    if (
        new_provider_type == CalendarProviderType.OUTLOOK
        and not config.outlook_calendar_connected
    ):
        raise HTTPException(
            status_code=400,
            detail="Outlook Calendar must be connected before activation",
        )

    config.active_calendar_provider = new_provider_type
    await db.commit()

    if (
        old_provider != request.provider
        and old_provider != "NONE"
        and request.provider != "NONE"
    ):
        from app.models.organization import Organization

        org_result = await db.execute(
            select(Organization).where(Organization.id == user.org_id)
        )
        org = org_result.scalars().first()
        org_name = org.name if org else "Booking"

        background_tasks.add_task(
            _run_migration_task,
            db.get_bind(),
            str(user.org_id),
            old_provider,
            request.provider,
            org_name,
        )

    return {
        "status": "updated",
        "active_provider": request.provider,
        "migration_triggered": old_provider != request.provider
        and old_provider != "NONE"
        and request.provider != "NONE",
    }


# ══════════════════════════════════════════════════
#  MEETING PROVIDER INTEGRATION (separate from calendar sync)
# ══════════════════════════════════════════════════


class MeetingProviderConfigResponse(BaseModel):
    google_meet_connected: bool = False
    microsoft_teams_connected: bool = False
    zoom_connected: bool = False
    active_meeting_provider: str = "NONE"


class SetActiveMeetingProviderRequest(BaseModel):
    provider: str  # "NONE", "GOOGLE_MEET", "MICROSOFT_TEAMS", or "ZOOM"


@router.get("/meeting-providers/config", response_model=MeetingProviderConfigResponse)
async def get_meeting_provider_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get meeting provider integration status (separate from calendar sync)."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        return MeetingProviderConfigResponse()

    active_mp = getattr(config, "active_meeting_provider", ActiveMeetingProvider.NONE)
    active_mp_value = (
        active_mp.value if hasattr(active_mp, "value") else str(active_mp or "NONE")
    )

    return MeetingProviderConfigResponse(
        google_meet_connected=getattr(config, "google_meet_connected", False) or False,
        microsoft_teams_connected=getattr(config, "microsoft_teams_connected", False)
        or False,
        zoom_connected=getattr(config, "zoom_connected", False) or False,
        active_meeting_provider=active_mp_value,
    )


@router.put("/meeting-providers/active-provider")
async def set_active_meeting_provider(
    request: SetActiveMeetingProviderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Set the active meeting provider for generating meeting links.
    Enforces connected-before-activate invariant.
    """
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    try:
        new_provider = ActiveMeetingProvider(request.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider value")

    # Enforce connected-before-activate (NONE is always allowed)
    if (
        new_provider == ActiveMeetingProvider.GOOGLE_MEET
        and not config.google_meet_connected
    ):
        raise HTTPException(
            status_code=400, detail="Google Meet must be connected before activation"
        )
    if (
        new_provider == ActiveMeetingProvider.MICROSOFT_TEAMS
        and not config.microsoft_teams_connected
    ):
        raise HTTPException(
            status_code=400,
            detail="Microsoft Teams must be connected before activation",
        )
    if new_provider == ActiveMeetingProvider.ZOOM and not config.zoom_connected:
        raise HTTPException(
            status_code=400, detail="Zoom must be connected before activation"
        )

    config.active_meeting_provider = new_provider
    config.meeting_provider_updated_at = datetime.now(timezone.utc)
    config.meeting_provider_updated_by = user.email
    await db.commit()

    return {
        "status": "updated",
        "active_meeting_provider": new_provider.value,
    }


@router.post("/meeting-providers/google-meet/connect")
async def connect_google_meet(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate Google Meet OAuth flow.
    Uses same OAuth as Google Calendar but for Meet-specific scopes.
    """
    from app.core.integrations.google_calendar import get_google_auth_url

    try:
        # Reuse Google Calendar OAuth flow (Meet is part of Google Workspace)
        auth_url = get_google_auth_url(
            state=_build_oauth_state(request, user.org_id, prefix="meet"),
            redirect_uri=_callback_url(request, "google_calendar_callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"auth_url": auth_url}


@router.post("/meeting-providers/google-meet/disconnect")
async def disconnect_google_meet(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Google Meet integration."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if config:
        config.google_meet_connected = False
        if config.active_meeting_provider == ActiveMeetingProvider.GOOGLE_MEET:
            config.active_meeting_provider = ActiveMeetingProvider.NONE
        config.meeting_provider_updated_at = datetime.now(timezone.utc)
        config.meeting_provider_updated_by = user.email
        await db.commit()

    return {"status": "disconnected"}


@router.post("/meeting-providers/microsoft-teams/connect")
async def connect_microsoft_teams(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate Microsoft Teams OAuth flow.
    Uses same OAuth as Outlook Calendar but for Teams-specific scopes.
    """
    from app.core.integrations.calendar_providers.outlook import OutlookCalendarProvider

    provider = OutlookCalendarProvider()
    try:
        # Reuse Outlook OAuth flow (Teams is part of Microsoft 365)
        auth_url = provider.get_auth_url(
            state=_build_oauth_state(request, user.org_id, prefix="teams"),
            redirect_uri=_callback_url(request, "outlook_calendar_callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"auth_url": auth_url}


@router.post("/meeting-providers/microsoft-teams/disconnect")
async def disconnect_microsoft_teams(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Microsoft Teams integration."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if config:
        config.microsoft_teams_connected = False
        if config.active_meeting_provider == ActiveMeetingProvider.MICROSOFT_TEAMS:
            config.active_meeting_provider = ActiveMeetingProvider.NONE
        config.meeting_provider_updated_at = datetime.now(timezone.utc)
        config.meeting_provider_updated_by = user.email
        await db.commit()

    return {"status": "disconnected"}


@router.post("/meeting-providers/zoom/connect")
async def connect_zoom(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate Zoom OAuth flow.
    Placeholder — actual OAuth implementation to be added when Zoom credentials are configured.
    """
    from app.core.integrations.meeting_providers.zoom import ZoomProvider

    provider = ZoomProvider()
    try:
        auth_url = provider.get_auth_url(
            state=_build_oauth_state(request, user.org_id, prefix="zoom"),
            redirect_uri=_callback_url(request, "zoom_callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"auth_url": auth_url}


@router.get("/meeting-providers/zoom/callback")
async def zoom_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """OAuth callback endpoint for Zoom meeting provider."""
    from fastapi.responses import RedirectResponse
    from app.core.integrations.meeting_providers.zoom import ZoomProvider

    org_id, _, runtime_frontend_url = _parse_oauth_state(state)

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    config = result.scalars().first()

    if not config:
        logger.error(f"Zoom callback: OrgConfig not found for org_id={org_id}")
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="org_not_found"
            ),
            status_code=303,
        )

    provider = ZoomProvider()
    try:
        success = await provider.handle_callback(
            code,
            config,
            db,
            redirect_uri=_callback_url(request, "zoom_callback"),
        )
    except RuntimeError as exc:
        logger.error(f"Zoom callback runtime error: {exc}")
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="zoom_misconfigured"
            ),
            status_code=303,
        )

    if success:
        config.meeting_provider_updated_at = datetime.now(timezone.utc)
        config.meeting_provider_updated_by = "oauth_callback"
        await db.commit()
        logger.info(f"Zoom connected for org_id={org_id}")
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, zoom_connected="true"
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=_integrations_redirect_url(
            frontend_url=runtime_frontend_url, error="zoom_connection_failed"
        ),
        status_code=303,
    )


@router.post("/meeting-providers/zoom/disconnect")
async def disconnect_zoom(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Zoom integration."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if config:
        config.zoom_connected = False
        config.zoom_refresh_token = None
        config.zoom_access_token = None
        config.zoom_token_expires_at = None
        if config.active_meeting_provider == ActiveMeetingProvider.ZOOM:
            config.active_meeting_provider = ActiveMeetingProvider.NONE
        config.meeting_provider_updated_at = datetime.now(timezone.utc)
        config.meeting_provider_updated_by = user.email
        await db.commit()

    return {"status": "disconnected"}


# ══════════════════════════════════════════════════
#  OUTLOOK CALENDAR INTEGRATION
# ══════════════════════════════════════════════════


class OutlookCalendarConfigResponse(BaseModel):
    outlook_calendar_enabled: bool = False
    outlook_calendar_connected: bool = False


@router.get("/outlook-calendar/config", response_model=OutlookCalendarConfigResponse)
async def get_outlook_calendar_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get Outlook Calendar integration status."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        return OutlookCalendarConfigResponse()

    return OutlookCalendarConfigResponse(
        outlook_calendar_enabled=config.outlook_calendar_enabled or False,
        outlook_calendar_connected=config.outlook_calendar_connected or False,
    )


@router.post("/outlook-calendar/connect")
async def connect_outlook_calendar(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Initiate Outlook Calendar OAuth flow."""
    from app.core.integrations.calendar_providers.outlook import OutlookCalendarProvider

    provider = OutlookCalendarProvider()
    try:
        auth_url = provider.get_auth_url(
            state=_build_oauth_state(request, user.org_id),
            redirect_uri=_callback_url(request, "outlook_calendar_callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"auth_url": auth_url}


@router.post("/outlook-calendar/disconnect")
async def disconnect_outlook_calendar(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Outlook Calendar integration."""
    from app.core.integrations.calendar_providers.migration_task import (
        cleanup_old_provider_events,
    )
    from app.models.organization import Organization

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if config:
        org_result = await db.execute(
            select(Organization).where(Organization.id == user.org_id)
        )
        org = org_result.scalars().first()

        if config.outlook_calendar_connected:
            deleted_count = await cleanup_old_provider_events(
                db, config, CalendarProviderType.OUTLOOK
            )
            logger.info(
                f"Cleaned up {deleted_count} Outlook events for org {user.org_id}"
            )

        config.outlook_calendar_connected = False
        config.outlook_calendar_enabled = False
        config.outlook_calendar_refresh_token = None
        config.outlook_calendar_access_token = None
        config.outlook_calendar_token_expires_at = None
        if config.active_calendar_provider == CalendarProviderType.OUTLOOK:
            config.active_calendar_provider = CalendarProviderType.NONE
        await db.commit()

    return {"status": "disconnected"}


@router.get("/outlook-calendar/callback")
async def outlook_calendar_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """OAuth callback endpoint for Outlook Calendar."""
    from fastapi.responses import RedirectResponse
    from app.core.integrations.calendar_providers.outlook import OutlookCalendarProvider

    org_id, state_prefix, runtime_frontend_url = _parse_oauth_state(state)
    is_teams_state = state_prefix == "teams"

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    config = result.scalars().first()

    if not config:
        logger.error(
            f"Outlook Calendar callback: OrgConfig not found for org_id={org_id}"
        )
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="org_not_found"
            ),
            status_code=303,
        )

    provider = OutlookCalendarProvider()
    try:
        success = await provider.handle_callback(
            code,
            config,
            db,
            redirect_uri=_callback_url(request, "outlook_calendar_callback"),
        )
    except RuntimeError as exc:
        logger.error(f"Outlook Calendar callback runtime error: {exc}")
        error_code = (
            "outlook_unauthorized_client"
            if str(exc) == "outlook_unauthorized_client"
            else "outlook_misconfigured"
        )
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error=error_code
            ),
            status_code=303,
        )

    if success:
        if is_teams_state:
            config.microsoft_teams_connected = True
            config.meeting_provider_updated_at = datetime.now(timezone.utc)
            config.meeting_provider_updated_by = "oauth_callback"
            await db.commit()
        elif config.active_calendar_provider in (None, CalendarProviderType.NONE):
            config.active_calendar_provider = CalendarProviderType.OUTLOOK
            await db.commit()
        logger.info(f"Outlook Calendar connected for org_id={org_id}")
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, outlook_connected="true"
            ),
            status_code=303,
        )
    else:
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="outlook_connection_failed"
            ),
            status_code=303,
        )


# ══════════════════════════════════════════════════
#  WIDGET TOKEN (for secure embed snippet)
# ══════════════════════════════════════════════════


def _generate_widget_token() -> str:
    """Generate a secure random widget token with ds_wid_ prefix."""
    import secrets

    return f"ds_wid_{secrets.token_urlsafe(24)}"


class WidgetTokenResponse(BaseModel):
    widget_token: str
    masked_token: str


@router.get("/widget-token", response_model=WidgetTokenResponse)
async def get_widget_token(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the current widget token for this organization.
    If no token exists, one is automatically generated.
    """
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    if not config.widget_token:
        config.widget_token = _generate_widget_token()
        await db.commit()
        await db.refresh(config)

    masked = config.widget_token[:8] + "..." + config.widget_token[-4:]
    return WidgetTokenResponse(
        widget_token=config.widget_token,
        masked_token=masked,
    )


@router.post("/widget-token/regenerate", response_model=WidgetTokenResponse)
async def regenerate_widget_token(
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    """
    Regenerate (rotate) the widget token.
    WARNING: The old token will stop working immediately.
    All websites using the old token will need to be updated.
    """
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config.widget_token = _generate_widget_token()
    await db.commit()
    await db.refresh(config)

    masked = config.widget_token[:8] + "..." + config.widget_token[-4:]
    return WidgetTokenResponse(
        widget_token=config.widget_token,
        masked_token=masked,
    )


@router.delete("/widget-token")
async def revoke_widget_token(
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the widget token. Widget will stop working until a new token is generated."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()

    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")

    config.widget_token = None
    await db.commit()

    return {"status": "revoked"}


class OrgApiKeyResponse(BaseModel):
    api_key: str
    masked_key: str
    old_key_valid_until: Optional[str] = None


def _mask_org_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return key[:6] + "..." + key[-4:]


@router.get("/api-key", response_model=OrgApiKeyResponse)
async def get_org_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the organization's full API key.
    The full key is only returned here (authenticated dashboard request) — the
    agent-config response only carries a masked preview.
    """
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    valid_until = None
    if getattr(org, "old_key_expires_at", None):
        valid_until = org.old_key_expires_at.isoformat()

    return OrgApiKeyResponse(
        api_key=org.api_key,
        masked_key=_mask_org_api_key(org.api_key),
        old_key_valid_until=valid_until,
    )


@router.post("/api-key/regenerate", response_model=OrgApiKeyResponse)
async def regenerate_org_api_key(
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    """
    Rotate the org API key. The current key moves to a 7-day grace window
    (old_api_key), then a new key is issued.
    WARNING: Existing integrations using the old key keep working for 7 days.
    """
    from app.crud.organization import rotate_api_key

    try:
        org = await rotate_api_key(db, str(user.org_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    valid_until = None
    if getattr(org, "old_key_expires_at", None):
        valid_until = org.old_key_expires_at.isoformat()

    return OrgApiKeyResponse(
        api_key=org.api_key,
        masked_key=_mask_org_api_key(org.api_key),
        old_key_valid_until=valid_until,
    )


@router.get("/google-calendar/callback")
async def google_calendar_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """
    OAuth callback endpoint for Google Calendar.

    After user authorizes, Google redirects here with:
    - code: authorization code to exchange for tokens
    - state: org_id to associate the token with
    """
    from fastapi.responses import RedirectResponse
    from app.core.integrations.google_calendar import exchange_code_for_tokens

    org_id, state_prefix, runtime_frontend_url = _parse_oauth_state(state)
    is_meet_state = state_prefix == "meet"

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    config = result.scalars().first()

    if not config:
        logger.error(
            f"Google Calendar callback: OrgConfig not found for org_id={org_id}"
        )
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="org_not_found"
            ),
            status_code=303,
        )

    if not code:
        logger.error("Google Calendar callback: No authorization code received")
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="no_code"
            ),
            status_code=303,
        )
    try:
        tokens = await exchange_code_for_tokens(
            code,
            redirect_uri=_callback_url(request, "google_calendar_callback"),
        )
    except RuntimeError as exc:
        logger.error(f"Google Calendar callback config error: {exc}")
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="google_misconfigured"
            ),
            status_code=303,
        )

    if not tokens or "refresh_token" not in tokens:
        logger.error(
            f"Google Calendar callback: Failed to get tokens. tokens_present={bool(tokens)} refresh_token_present={'refresh_token' in tokens if tokens else False}"
        )
        return RedirectResponse(
            url=_integrations_redirect_url(
                frontend_url=runtime_frontend_url, error="token_exchange_failed"
            ),
            status_code=303,
        )

    config.google_calendar_refresh_token = tokens["refresh_token"]
    config.google_calendar_connected = True
    config.google_calendar_enabled = True
    if is_meet_state:
        config.google_meet_connected = True
        config.meeting_provider_updated_at = datetime.now(timezone.utc)
        config.meeting_provider_updated_by = "oauth_callback"
    elif config.active_calendar_provider in (None, CalendarProviderType.NONE):
        config.active_calendar_provider = CalendarProviderType.GOOGLE
    await db.commit()

    logger.info(f"Google Calendar connected for org_id={org_id}")
    return RedirectResponse(
        url=_integrations_redirect_url(
            frontend_url=runtime_frontend_url, google_connected="true"
        ),
        status_code=303,
    )


# ══════════════════════════════════════════════════
#  BUSINESS SCHEDULE — Virtual Slot Engine
# ══════════════════════════════════════════════════


class ScheduleDayItem(BaseModel):
    day_of_week: int  # 0=Mon … 6=Sun
    is_active: bool
    open_time: str  # "HH:MM"
    close_time: str  # "HH:MM"
    break_start: Optional[str] = None
    break_end: Optional[str] = None


class ScheduleResponse(BaseModel):
    slot_duration_minutes: int
    booking_window_days: int
    timezone: str
    days: List[ScheduleDayItem]


class ScheduleUpdate(BaseModel):
    slot_duration_minutes: Optional[int] = None
    booking_window_days: Optional[int] = None
    timezone: Optional[str] = None
    days: Optional[List[ScheduleDayItem]] = None


class ExceptionItem(BaseModel):
    id: Optional[str] = None
    exception_date: str  # "YYYY-MM-DD"
    exception_type: str  # "CLOSED" or "CUSTOM_HOURS"
    custom_open: Optional[str] = None
    custom_close: Optional[str] = None
    reason: Optional[str] = None


class SlotPreviewItem(BaseModel):
    date: str
    available_times: List[str]


def _time_to_str(t) -> Optional[str]:
    if t is None:
        return None
    return t.strftime("%H:%M")


@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the org's weekly business schedule."""
    from app.models.booking import BusinessSchedule, ScheduleDay

    sched_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == user.org_id)
    )
    sched = sched_result.scalars().first()

    days_result = await db.execute(
        select(ScheduleDay)
        .where(ScheduleDay.org_id == user.org_id)
        .order_by(ScheduleDay.day_of_week)
    )
    days = days_result.scalars().all()

    day_items = [
        ScheduleDayItem(
            day_of_week=d.day_of_week,
            is_active=d.is_active,
            open_time=_time_to_str(d.open_time),
            close_time=_time_to_str(d.close_time),
            break_start=_time_to_str(d.break_start),
            break_end=_time_to_str(d.break_end),
        )
        for d in days
    ]

    # If the org has no schedule days yet, return sensible defaults
    # (Mon–Fri open 09:00–17:00, weekend closed) so the UI always shows a
    # full week and a save persists real rows. Without this an org can end
    # up with a BusinessSchedule but zero ScheduleDay rows, which makes the
    # booking engine treat every day as closed and show no time slots.
    if not day_items:
        day_items = [
            ScheduleDayItem(
                day_of_week=weekday,
                is_active=weekday < 5,
                open_time="09:00",
                close_time="17:00",
                break_start=None,
                break_end=None,
            )
            for weekday in range(7)
        ]

    return ScheduleResponse(
        slot_duration_minutes=sched.slot_duration_minutes if sched else 60,
        booking_window_days=sched.booking_window_days if sched else 60,
        timezone=sched.timezone if sched else "UTC",
        days=day_items,
    )


@router.put("/schedule", response_model=ScheduleResponse)
async def update_schedule(
    payload: ScheduleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the org's weekly business schedule."""
    from app.models.booking import BusinessSchedule, ScheduleDay
    from datetime import time as dt_time

    def parse_time(s: Optional[str]) -> Optional[dt_time]:
        if not s:
            return None
        h, m = s.split(":")
        return dt_time(int(h), int(m))

    # Upsert BusinessSchedule
    sched_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == user.org_id)
    )
    sched = sched_result.scalars().first()
    if not sched:
        sched = BusinessSchedule(org_id=user.org_id)
        db.add(sched)

    if payload.slot_duration_minutes is not None:
        sched.slot_duration_minutes = payload.slot_duration_minutes
    if payload.booking_window_days is not None:
        sched.booking_window_days = payload.booking_window_days
    if payload.timezone is not None:
        sched.timezone = payload.timezone

    # Business Hours is the single source of truth for timezone + booking window.
    # Mirror those values into OrgConfig so agent/widget code paths stay in sync.
    if payload.timezone is not None or payload.booking_window_days is not None:
        org_config_result = await db.execute(
            select(OrgConfig).where(OrgConfig.org_id == user.org_id)
        )
        org_config = org_config_result.scalars().first()
        if org_config:
            if payload.timezone is not None:
                org_config.timezone = payload.timezone
            if payload.booking_window_days is not None:
                org_config.max_booking_days_ahead = payload.booking_window_days

    # Upsert ScheduleDay rows
    if payload.days is not None:
        for day_data in payload.days:
            day_result = await db.execute(
                select(ScheduleDay).where(
                    ScheduleDay.org_id == user.org_id,
                    ScheduleDay.day_of_week == day_data.day_of_week,
                )
            )
            day = day_result.scalars().first()
            if not day:
                day = ScheduleDay(
                    org_id=user.org_id,
                    day_of_week=day_data.day_of_week,
                    open_time=parse_time("09:00"),
                    close_time=parse_time("17:00"),
                )
                db.add(day)
            day.is_active = day_data.is_active
            day.open_time = parse_time(day_data.open_time)
            day.close_time = parse_time(day_data.close_time)
            day.break_start = parse_time(day_data.break_start)
            day.break_end = parse_time(day_data.break_end)

    await db.commit()
    await db.refresh(sched)

    days_result = await db.execute(
        select(ScheduleDay)
        .where(ScheduleDay.org_id == user.org_id)
        .order_by(ScheduleDay.day_of_week)
    )
    days = days_result.scalars().all()

    return ScheduleResponse(
        slot_duration_minutes=sched.slot_duration_minutes,
        booking_window_days=sched.booking_window_days,
        timezone=sched.timezone,
        days=[
            ScheduleDayItem(
                day_of_week=d.day_of_week,
                is_active=d.is_active,
                open_time=_time_to_str(d.open_time),
                close_time=_time_to_str(d.close_time),
                break_start=_time_to_str(d.break_start),
                break_end=_time_to_str(d.break_end),
            )
            for d in days
        ],
    )


@router.get("/schedule/exceptions", response_model=List[ExceptionItem])
async def list_exceptions(
    year: Optional[int] = Query(default=None),
    month: Optional[int] = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List schedule exceptions, optionally filtered by year/month."""
    from app.models.booking import ScheduleException
    from sqlalchemy import extract

    q = select(ScheduleException).where(ScheduleException.org_id == user.org_id)
    if year:
        q = q.where(extract("year", ScheduleException.exception_date) == year)
    if month:
        q = q.where(extract("month", ScheduleException.exception_date) == month)
    result = await db.execute(q.order_by(ScheduleException.exception_date))
    excs = result.scalars().all()

    return [
        ExceptionItem(
            id=str(e.id),
            exception_date=e.exception_date.isoformat(),
            exception_type=e.exception_type.value,
            custom_open=_time_to_str(e.custom_open),
            custom_close=_time_to_str(e.custom_close),
            reason=e.reason,
        )
        for e in excs
    ]


@router.post("/schedule/exceptions", response_model=ExceptionItem, status_code=201)
async def create_exception(
    payload: ExceptionItem,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace a schedule exception for a specific date."""
    from app.models.booking import ScheduleException, ExceptionType, TimeSlot
    from datetime import date as dt_date, time as dt_time
    from sqlalchemy import select, func
    from fastapi import HTTPException

    exc_date = dt_date.fromisoformat(payload.exception_date)

    def parse_time(s: Optional[str]) -> Optional[dt_time]:
        if not s:
            return None
        h, m = s.split(":")
        return dt_time(int(h), int(m))

    # Check for existing bookings on this date
    booked_slots_query = select(TimeSlot).where(
        TimeSlot.org_id == user.org_id,
        func.date(TimeSlot.start_time) == exc_date,
        TimeSlot.is_booked == True,
    )
    booked_slots_result = await db.execute(booked_slots_query)
    booked_slots = booked_slots_result.scalars().all()

    if booked_slots:
        if payload.exception_type == "CLOSED":
            raise HTTPException(
                status_code=409,
                detail=f"Action Blocked: There are {len(booked_slots)} existing bookings on this date. Please reschedule them first.",
            )
        else:
            c_open = parse_time(payload.custom_open)
            c_close = parse_time(payload.custom_close)
            if not c_open or not c_close:
                raise HTTPException(
                    status_code=400,
                    detail="Custom hours require both open and close times.",
                )
            invalid_slots = [
                s
                for s in booked_slots
                if s.start_time.time() < c_open or s.end_time.time() > c_close
            ]
            if invalid_slots:
                raise HTTPException(
                    status_code=409,
                    detail=f"Action Blocked: {len(invalid_slots)} existing bookings fall outside your new custom hours. Please reschedule them first.",
                )

    # Delete existing exception for same date (upsert)
    existing_result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.org_id == user.org_id,
            ScheduleException.exception_date == exc_date,
        )
    )
    existing = existing_result.scalars().first()
    if existing:
        await db.delete(existing)
        await (
            db.flush()
        )  # CRITICAL: Flush delete before insert to avoid unique constraint violations

    exc = ScheduleException(
        org_id=user.org_id,
        exception_date=exc_date,
        exception_type=ExceptionType(payload.exception_type),
        custom_open=parse_time(payload.custom_open),
        custom_close=parse_time(payload.custom_close),
        reason=payload.reason,
    )
    db.add(exc)
    await db.commit()
    await db.refresh(exc)

    return ExceptionItem(
        id=str(exc.id),
        exception_date=exc.exception_date.isoformat(),
        exception_type=exc.exception_type.value,
        custom_open=_time_to_str(exc.custom_open),
        custom_close=_time_to_str(exc.custom_close),
        reason=exc.reason,
    )


@router.delete("/schedule/exceptions/{exception_id}", status_code=204)
async def delete_exception(
    exception_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a schedule exception by ID."""
    from app.models.booking import ScheduleException
    import uuid as _uuid

    result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.id == _uuid.UUID(exception_id),
            ScheduleException.org_id == user.org_id,
        )
    )
    exc = result.scalars().first()
    if not exc:
        raise HTTPException(status_code=404, detail="Exception not found")

    await db.delete(exc)
    await db.commit()


@router.get("/schedule/preview", response_model=List[SlotPreviewItem])
async def preview_slots(
    start_date: str = Query(description="YYYY-MM-DD"),
    days: int = Query(default=14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Preview available slots for a date range, as computed by the slot engine.
    Useful for the admin to verify the schedule before it goes live.
    """
    from app.engine.slot_engine import compute_available_slots, format_slot_time
    from datetime import date as dt_date, timedelta

    try:
        start = dt_date.fromisoformat(start_date)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
        )

    results = []
    for i in range(days):
        check_date = start + timedelta(days=i)
        times = await compute_available_slots(db, user.org_id, check_date)
        results.append(
            SlotPreviewItem(
                date=check_date.isoformat(),
                available_times=[format_slot_time(t) for t in times],
            )
        )

    return results


# ══════════════════════════════════════════════════
#  CALENDAR EVENTS
# ══════════════════════════════════════════════════


@router.get("/calendar-events", response_model=List[CalendarEventItem])
async def get_calendar_events(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return all confirmed bookings in a date range (inclusive) for the calendar grid.
    Joins TimeSlot for slot times and ChatSession for collected form data.
    """
    from datetime import date as dt_date

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
        )

    # Join Booking → TimeSlot to filter by date range
    stmt = (
        select(Booking, TimeSlot, ChatSession)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .outerjoin(ChatSession, Booking.session_id == ChatSession.id)
        .where(
            Booking.org_id == user.org_id,
            TimeSlot.start_time >= start_dt,
            TimeSlot.start_time <= end_dt,
        )
        .order_by(TimeSlot.start_time.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for booking, slot, session in rows:
        collected = {}
        if session and session.state_data:
            collected = {
                k: v
                for k, v in session.state_data.items()
                if k not in ("otp_code", "otp_hash", "_internal")
            }
        items.append(
            CalendarEventItem(
                id=str(booking.id),
                customer_name=booking.customer_name or "Unknown",
                customer_email=booking.customer_email or "",
                customer_phone=booking.customer_phone,
                slot_start=slot.start_time if slot else None,
                slot_end=slot.end_time if slot else None,
                created_at=booking.created_at,
                collected_data=collected,
            )
        )
    return items


@router.get("/upcoming-bookings", response_model=List[CalendarEventItem])
async def get_upcoming_bookings(
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD, defaults to today"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Paginated list of upcoming confirmed bookings from `from_date` forward.
    Defaults to today. Used by the upcoming bookings panel in the calendar UI.
    """
    if from_date:
        try:
            # Handle YYYY-MM-DD format and make it start of day in UTC
            dt = datetime.strptime(from_date, "%Y-%m-%d")
            cutoff = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                cutoff = datetime.fromisoformat(from_date)
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid from_date. Use YYYY-MM-DD."
                )
    else:
        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    stmt = (
        select(Booking, TimeSlot, ChatSession)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .outerjoin(ChatSession, Booking.session_id == ChatSession.id)
        .where(
            Booking.org_id == user.org_id,
            TimeSlot.start_time >= cutoff,
        )
        .order_by(TimeSlot.start_time.asc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for booking, slot, session in rows:
        collected = {}
        if session and session.state_data:
            collected = {
                k: v
                for k, v in session.state_data.items()
                if k not in ("otp_code", "otp_hash", "_internal")
            }
        items.append(
            CalendarEventItem(
                id=str(booking.id),
                customer_name=booking.customer_name or "Unknown",
                customer_email=booking.customer_email or "",
                customer_phone=booking.customer_phone,
                slot_start=slot.start_time if slot else None,
                slot_end=slot.end_time if slot else None,
                created_at=booking.created_at,
                collected_data=collected,
            )
        )
    return items
