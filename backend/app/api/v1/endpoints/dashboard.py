"""
Dashboard API — all endpoints scoped to the current user's organization.
"""

import logging
import os
import hmac
import hashlib
import uuid as _uuid
from pathlib import Path
from datetime import datetime, time, timezone
from typing import Optional, List, Any, Dict

def _parse_time(value: Optional[str]) -> Optional[time]:
    """Parse HH:MM string to time object."""
    if value is None or value == "":
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


# ── Calendar "Additional Data" ──────────────────────────────────────────────
# The chat session's state_data carries the whole engine state: raw UUIDs,
# cooldown counters, the WhatsApp JID, the agent's last reply, timezone
# plumbing. Almost none of it means anything to a clinic admin, and a
# block-list kept leaking new internals (a raw unit id, a duplicate phone
# number, an internal flag).
#
# So this is an ALLOW-list: the department, the team member and the
# tenant's own custom booking fields - nothing else. A new internal field
# can never reach the UI by accident.
_CALENDAR_EXTRA_FIELDS = frozenset({"unit_name", "provider_name"})

# The core booking fields are already shown in the dialog's own cards
# (customer, email, phone, date, time), so they are not repeated here.
_CORE_BOOKING_FIELDS = frozenset({"name", "email", "phone", "date", "time"})

# Kept for backwards compatibility with anything that imports it.
_INTERNAL_COLLECTED_KEYS = {
    "otp_code", "otp_hash", "_internal",
    "user_timezone", "locked_slot_id", "locked_slot_start_utc", "locked_slot_end_utc",
    "timezone_source", "timezone_fallback_used", "timezone_fallback_reason",
    "selected_slot_id", "selected_slot_start_utc", "runtime_checkpoint",
    "active_path_effects", "active_path", "checkpoint",
    "field_definitions_snapshot", "field_definitions", "field_snapshot", "slot_id",
}
_INTERNAL_KEY_SUBSTRINGS = ("checkpoint", "path_effects", "snapshot", "slot_id", "locked_slot")


async def _calendar_extra_field_names(db: "AsyncSession", org_id: Any) -> set:
    """Custom booking fields the tenant defined (e.g. "Organisation Name").

    These are collected by the tenant on purpose, so the admin does want to
    see them in the calendar dialog.
    """
    try:
        res = await db.execute(
            select(OrgConfig.field_definitions).where(OrgConfig.org_id == org_id)
        )
        raw = res.scalars().first()
    except Exception:
        return set()
    names = set()
    for fd in (raw or []):
        if not isinstance(fd, dict):
            continue
        name = str(fd.get("name") or "").strip()
        if name and name not in _CORE_BOOKING_FIELDS:
            names.add(name)
    return names


def _filter_collected_data(raw: dict, allowed: Optional[set] = None) -> dict:
    """Return only the user-facing booking fields for the calendar dialog.

    Allow-list: the department, the team member and the tenant's own
    custom fields. Everything else in the session state is engine
    internals and stays on the server.
    """
    if not isinstance(raw, dict):
        return {}
    keep = set(_CALENDAR_EXTRA_FIELDS)
    keep |= {str(k) for k in (allowed or set())}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in keep:
            continue
        if v is None or v == "":
            continue
        if isinstance(v, (dict, list)):
            continue  # hide [object Object] dumps
        out[k] = v
    return out
from urllib.parse import quote, unquote, urlsplit
from pydantic import BaseModel, Field, ValidationError, field_validator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func as sa_func, or_, desc, asc, String

from app.db.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.session import ChatSession
from app.models.booking import Booking, TimeSlot, BusinessSchedule, ScheduleDay
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

async def _member_provider_id(db, user: User):
    """If user is a member (providers.user_id), return provider.id else None — for own-dashboard scoping."""
    try:
        from app.models.service_unit import Provider
        row = (await db.execute(select(Provider.id).where(Provider.user_id==user.id, Provider.org_id==user.org_id))).scalars().first()
        return row
    except Exception:
        return None


async def _member_scope(db, user: User) -> tuple:
    """(provider_id, unit_ids) for staff scoping.

    Returns (None, []) for owners/managers/individual users — meaning NO
    restriction, so individual orgs and old flows behave exactly as before.
    """
    provider_id = await _member_provider_id(db, user)
    unit_ids: list = []
    try:
        from app.models.staff import StaffUnitScope

        rows = (
            await db.execute(
                select(StaffUnitScope.unit_id).where(StaffUnitScope.user_id == user.id)
            )
        ).all()
        unit_ids = [r[0] for r in rows]
    except Exception:
        unit_ids = []
    return provider_id, unit_ids
MAX_SELECT_OPTIONS = 50
CORE_FIELD_NAMES = {"name", "email", "phone", "date", "time"}


def _clean_customer_value(value: Any) -> Optional[str]:
    """Return a non-empty string from a collected_data value, else None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


_CALLBACK_ENV_NAMES = {
    "google_calendar_callback": "GOOGLE_REDIRECT_URI",
    "outlook_calendar_callback": "MICROSOFT_REDIRECT_URI",
    "zoom_callback": "ZOOM_REDIRECT_URI",
}


def _callback_url(request: Request, route_name: str) -> str:
    env_name = _CALLBACK_ENV_NAMES.get(route_name)
    if env_name:
        env_value = (os.getenv(env_name) or "").strip()
        if env_value:
            return env_value
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


class DailyCount(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


class ChannelCount(BaseModel):
    channel: str
    count: int


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
    bookings_by_day: List[DailyCount] = []  # Last 14 days of bookings
    cancelled_by_day: List[DailyCount] = []  # Last 14 days of cancelled sessions
    channel_breakdown: List[ChannelCount] = []  # CONFIRMED sessions by channel
    web_bookings_by_day: List[DailyCount] = []  # Last 14 days of web bookings
    whatsapp_bookings_by_day: List[DailyCount] = []  # Last 14 days of WhatsApp bookings


class BookingItem(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    customer_phone: Optional[str]
    slot_start: Optional[datetime]
    slot_end: Optional[datetime]
    created_at: datetime
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None


class GlobalSearchBooking(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    customer_phone: Optional[str]
    slot_start: Optional[datetime]
    created_at: datetime


class GlobalSearchConversation(BaseModel):
    id: str
    session_status: str
    channel: str = "web"
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    updated_at: datetime


class GlobalSearchResponse(BaseModel):
    bookings: List[GlobalSearchBooking] = []
    conversations: List[GlobalSearchConversation] = []


class BookingPageItem(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    customer_phone: Optional[str]
    slot_start: Optional[datetime]
    slot_end: Optional[datetime]
    created_at: datetime
    session_id: Optional[str] = None
    session_status: Optional[str] = None
    meeting_type_snapshot: Optional[str] = None
    meeting_link_status: Optional[str] = None
    meeting_link_url: Optional[str] = None
    meeting_link_provider: Optional[str] = None
    attendance_status: Optional[str] = None
    attended_at: Optional[datetime] = None
    attendance_notes: Optional[str] = None
    cancellation_reason: Optional[str] = None
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None


class BookingPageResponse(BaseModel):
    items: List[BookingPageItem]
    total: int
    page: int
    page_size: int


class CancelledBookingItem(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    customer_phone: Optional[str] = None
    booking_date: Optional[str] = None
    booking_time: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    booking_id: Optional[str] = None
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None


class CancelledBookingPageResponse(BaseModel):
    items: List[CancelledBookingItem]
    total: int
    page: int
    page_size: int


class ConversationItem(BaseModel):
    id: str
    session_status: str
    current_intent: Optional[str]
    collected_data: dict
    missing_fields: list
    created_at: datetime
    updated_at: datetime
    channel: str = "web"
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None


class AgentConfigResponse(BaseModel):
    required_fields: list
    field_definitions: Optional[list] = None
    custom_prompts: Optional[dict] = None
    timezone: str
    ui_brand_color: str
    logo_url: Optional[str] = None
    org_name: str
    business_name: Optional[str] = None
    api_key: str
    require_otp: bool = True
    require_phone_otp: bool = False
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
    logo_url: Optional[str] = None
    org_name: Optional[str] = None
    business_name: Optional[str] = None
    require_otp: Optional[bool] = None
    require_phone_otp: Optional[bool] = None
    max_booking_days_ahead: Optional[int] = Field(default=None, ge=1, le=366)
    session_retention_days: Optional[int] = Field(default=None, ge=7, le=365)
    session_cleanup_frequency: Optional[str] = None
    session_cleanup_hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    meeting_mode: Optional[MeetingMode] = None
    allow_hybrid_selection: Optional[bool] = None
    meeting_provider_default: Optional[MeetingProviderDefault] = None

    @field_validator("max_booking_days_ahead")
    @classmethod
    def _leap_aware_horizon(cls, value):
        return _check_horizon_cap(value, "max_booking_days_ahead")


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
    days: int = Query(default=14, ge=7, le=90),
):
    """Overview statistics for the current org.

    Staff scoping: a doctor login sees only own bookings/sessions, a dept
    manager only his units. Owners and individual users have no scope, so
    their numbers are byte-identical to before this change.
    """
    org_id = user.org_id
    _scope_provider, _scope_units = await _member_scope(db, user)

    def _scoped(model, *extra):
        preds = [model.org_id == org_id, *extra]
        if _scope_provider is not None and hasattr(model, "provider_id"):
            preds.append(model.provider_id == _scope_provider)
        elif _scope_units and hasattr(model, "unit_id"):
            preds.append(model.unit_id.in_(_scope_units))
        return preds

    # Total bookings
    total_bookings_sq = (
        select(sa_func.count())
        .select_from(Booking)
        .where(*_scoped(Booking))
        .scalar_subquery()
    )

    active_sessions_sq = (
        select(sa_func.count())
        .select_from(ChatSession)
        .where(*_scoped(ChatSession, ChatSession.session_status == "ACTIVE"))
        .scalar_subquery()
    )
    confirmed_sessions_sq = (
        select(sa_func.count())
        .select_from(ChatSession)
        .where(*_scoped(ChatSession, ChatSession.session_status == "CONFIRMED"))
        .scalar_subquery()
    )
    cancelled_sessions_sq = (
        select(sa_func.count())
        .select_from(ChatSession)
        .where(*_scoped(ChatSession, ChatSession.session_status == "CANCELLED"))
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
        .where(*_scoped(Booking, Booking.created_at >= week_ago))
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

    # ── Daily series (last N days) + channel breakdown ──
    from sqlalchemy import cast as sa_cast, Date as sa_date

    days_ago = datetime.now(timezone.utc) - timedelta(days=days - 1)

    booking_rows = await db.execute(
        select(sa_cast(Booking.created_at, sa_date).label("day"), sa_func.count().label("c"))
        .where(*_scoped(Booking, Booking.created_at >= days_ago))
        .group_by("day")
    )
    cancelled_rows = await db.execute(
        select(sa_cast(ChatSession.created_at, sa_date).label("day"), sa_func.count().label("c"))
        .where(
            *_scoped(
                ChatSession,
                ChatSession.session_status == "CANCELLED",
                ChatSession.created_at >= days_ago,
            )
        )
        .group_by("day")
    )
    channel_rows = await db.execute(
        select(ChatSession.channel, sa_func.count())
        .where(
            *_scoped(
                ChatSession,
                ChatSession.session_status == "CONFIRMED",
                ChatSession.channel.isnot(None),
            )
        )
        .group_by(ChatSession.channel)
    )

    # Channel-specific bookings by day
    web_booking_rows = await db.execute(
        select(sa_cast(ChatSession.created_at, sa_date).label("day"), sa_func.count().label("c"))
        .where(
            *_scoped(
                ChatSession,
                ChatSession.session_status == "CONFIRMED",
                ChatSession.channel == "web",
                ChatSession.created_at >= days_ago,
            )
        )
        .group_by("day")
    )
    whatsapp_booking_rows = await db.execute(
        select(sa_cast(ChatSession.created_at, sa_date).label("day"), sa_func.count().label("c"))
        .where(
            *_scoped(
                ChatSession,
                ChatSession.session_status == "CONFIRMED",
                ChatSession.channel == "whatsapp",
                ChatSession.created_at >= days_ago,
            )
        )
        .group_by("day")
    )

    def fill_series(rows: list) -> List[DailyCount]:
        counts = {str(r[0]): r[1] for r in rows}
        start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
        out: List[DailyCount] = []
        for i in range(days):
            day = start + timedelta(days=i)
            out.append(DailyCount(date=day.isoformat(), count=counts.get(day.isoformat(), 0)))
        return out

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
        bookings_by_day=fill_series(booking_rows.all()),
        cancelled_by_day=fill_series(cancelled_rows.all()),
        channel_breakdown=[
            ChannelCount(channel=r[0] or "web", count=r[1]) for r in channel_rows.all()
        ],
        web_bookings_by_day=fill_series(web_booking_rows.all()),
        whatsapp_bookings_by_day=fill_series(whatsapp_booking_rows.all()),
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


class TopBreakdownItem(BaseModel):
    id: Optional[str] = None
    name: str
    bookings: int


class TopBreakdownResponse(BaseModel):
    units: List[TopBreakdownItem] = []
    providers: List[TopBreakdownItem] = []


class RevenueBreakdownItem(BaseModel):
    id: Optional[str] = None
    name: str
    role_label: Optional[str] = None
    bookings: int
    gross_revenue_paise: int
    collected_revenue_paise: int


class RevenueTotals(BaseModel):
    bookings: int
    gross_revenue_paise: int
    collected_revenue_paise: int


class RevenueResponse(BaseModel):
    days: int
    totals: RevenueTotals
    by_department: List[RevenueBreakdownItem] = []
    by_member: List[RevenueBreakdownItem] = []


@router.get("/revenue", response_model=RevenueResponse)
async def get_revenue(
    days: int = Query(default=30, ge=1, le=365),
    unit_id: Optional[str] = Query(
        default=None,
        description="When set, team-member revenue is scoped to this department's bookings only",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revenue per department and team member for the last N days.

    Two metrics, both org-scoped and staff-scoped like the rest of the
    dashboard (doctors see only their own numbers, department managers
    only their units):
      * gross revenue — the booked amount (provider fee or appointment
        price) of every booking that still counts. CANCELLED and
        RESCHEDULED rows are excluded: the first is void, the second is
        superseded by the new booking row and would double-charge.
      * collected revenue — the slice with payment_status "paid".

    ``unit_id`` narrows the team-member breakdown to that department's
    bookings (the department list and totals stay org-wide), matching
    the tap-a-department interaction of the Departments/Team cards.

    Bookings without a department or team member still carry revenue, so
    they are reported under "Unassigned" and the breakdown sums match the
    totals.
    """
    from datetime import timedelta
    from sqlalchemy import case as sa_case

    since = datetime.now(timezone.utc) - timedelta(days=days)
    _sp, _su = await _member_scope(db, user)
    base = [
        Booking.org_id == user.org_id,
        Booking.created_at >= since,
        # CANCELLED is void, RESCHEDULED rows are superseded by the new
        # booking row. NULL status (legacy rows) still counts as revenue.
        or_(
            Booking.attendance_status.is_(None),
            Booking.attendance_status.notin_(["CANCELLED", "RESCHEDULED"]),
        ),
    ]
    if _sp is not None:
        base.append(Booking.provider_id == _sp)
    elif _su:
        base.append(Booking.unit_id.in_(_su))

    gross_expr = sa_func.coalesce(Booking.amount_paise, 0)
    collected_expr = sa_case(
        (Booking.payment_status == "paid", sa_func.coalesce(Booking.amount_paise, 0)),
        else_=0,
    )

    async def _revenue_group(group_col, conds):
        return (
            await db.execute(
                select(
                    group_col,
                    sa_func.count().label("bookings"),
                    sa_func.coalesce(sa_func.sum(gross_expr), 0).label("gross"),
                    sa_func.coalesce(sa_func.sum(collected_expr), 0).label("paid"),
                )
                .where(*conds)
                .group_by(group_col)
                .order_by(sa_func.sum(gross_expr).desc())
            )
        ).all()

    total_row = (
        await db.execute(
            select(
                sa_func.count().label("bookings"),
                sa_func.coalesce(sa_func.sum(gross_expr), 0).label("gross"),
                sa_func.coalesce(sa_func.sum(collected_expr), 0).label("paid"),
            ).where(*base)
        )
    ).one()

    unit_rows = await _revenue_group(Booking.unit_id, base)

    member_base = list(base)
    if unit_id:
        try:
            import uuid as _uuid

            member_base.append(Booking.unit_id == _uuid.UUID(str(unit_id)))
        except Exception:
            pass
    prov_rows = await _revenue_group(Booking.provider_id, member_base)

    from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

    async def _name_map(model, ids, name_attr, extra_attr=None):
        out: Dict[Any, Dict[str, Any]] = {}
        real_ids = [i for i in ids if i is not None]
        if not real_ids:
            return out
        rows = (
            await db.execute(select(model).where(model.id.in_(real_ids)))
        ).scalars().all()
        for row in rows:
            out[row.id] = {
                "name": getattr(row, name_attr, "") or "",
                "extra": getattr(row, extra_attr, None) if extra_attr else None,
            }
        return out

    unit_ids = [r[0] for r in unit_rows]
    prov_ids = [r[0] for r in prov_rows]
    unit_names = await _name_map(_SU, unit_ids, "name")
    prov_names = await _name_map(_PV, prov_ids, "full_name", "role_label")

    def _items(rows, names, unassigned_label):
        items: List[RevenueBreakdownItem] = []
        for gid, count, gross, paid in rows:
            info = names.get(gid)
            items.append(
                RevenueBreakdownItem(
                    id=str(gid) if gid is not None else None,
                    name=(info["name"] if info and info.get("name") else unassigned_label),
                    role_label=(info.get("extra") if info else None),
                    bookings=int(count or 0),
                    gross_revenue_paise=int(gross or 0),
                    collected_revenue_paise=int(paid or 0),
                )
            )
        return items

    return RevenueResponse(
        days=days,
        totals=RevenueTotals(
            bookings=int(total_row.bookings or 0),
            gross_revenue_paise=int(total_row.gross or 0),
            collected_revenue_paise=int(total_row.paid or 0),
        ),
        by_department=_items(unit_rows, unit_names, "Unassigned"),
        by_member=_items(prov_rows, prov_names, "Unassigned"),
    )


@router.get("/top-breakdown", response_model=TopBreakdownResponse)
async def get_top_breakdown(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=5, ge=1, le=20),
    unit_id: Optional[str] = Query(default=None, description="When set, doctors are ranked within this department only"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Top departments + doctors by bookings in the last N days.

    Owner view for organization dashboards. Individual orgs have no
    units/providers, so both lists come back empty and the UI hides the
    strip — individual behavior unchanged.
    """
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(days=days)
    _sp, _su = await _member_scope(db, user)
    base = [Booking.org_id == user.org_id, Booking.created_at >= since]
    # Staff-scoped like the rest of the dashboard (doctor sees own only).
    if _sp is not None:
        base.append(Booking.provider_id == _sp)
    elif _su:
        base.append(Booking.unit_id.in_(_su))

    unit_rows = (
        await db.execute(
            select(Booking.unit_id, sa_func.count().label("c"))
            .where(*base, Booking.unit_id.isnot(None))
            .group_by(Booking.unit_id)
            .order_by(sa_func.count().desc())
            .limit(limit)
        )
    ).all()
    prov_base = list(base)
    if unit_id:
        try:
            import uuid as _uuid

            prov_base.append(Booking.unit_id == _uuid.UUID(str(unit_id)))
        except Exception:
            pass
    prov_rows = (
        await db.execute(
            select(Booking.provider_id, sa_func.count().label("c"))
            .where(*prov_base, Booking.provider_id.isnot(None))
            .group_by(Booking.provider_id)
            .order_by(sa_func.count().desc())
            .limit(limit)
        )
    ).all()

    from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

    units_out: List[TopBreakdownItem] = []
    if unit_rows:
        _umap = {
            r.id: r.name
            for r in (
                await db.execute(
                    select(_SU).where(_SU.id.in_([r[0] for r in unit_rows]))
                )
            ).scalars().all()
        }
        units_out = [
            TopBreakdownItem(id=str(r[0]), name=_umap.get(r[0], "Department"), bookings=r[1])
            for r in unit_rows
        ]
    providers_out: List[TopBreakdownItem] = []
    if prov_rows:
        _pmap = {
            r.id: r.full_name
            for r in (
                await db.execute(
                    select(_PV).where(_PV.id.in_([r[0] for r in prov_rows]))
                )
            ).scalars().all()
        }
        providers_out = [
            TopBreakdownItem(id=str(r[0]), name=_pmap.get(r[0], "Doctor"), bookings=r[1])
            for r in prov_rows
        ]
    return TopBreakdownResponse(units=units_out, providers=providers_out)


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
    meeting_type_snapshot: Optional[str] = None
    meeting_link_status: Optional[str] = None
    meeting_link_url: Optional[str] = None
    meeting_link_provider: Optional[str] = None
    # Org-mode: which department + doctor this booking belongs to
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None


@router.get("/recent-bookings", response_model=List[BookingItem])
async def get_recent_bookings(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent bookings for the current org (JOIN eliminates N+1).

    Staff-scoped: doctor logins see only their own recent bookings.
    """
    from sqlalchemy.orm import selectinload

    _sp, _su = await _member_scope(db, user)
    _preds = [Booking.org_id == user.org_id]
    if _sp is not None:
        _preds.append(Booking.provider_id == _sp)
    elif _su:
        _preds.append(Booking.unit_id.in_(_su))

    # Single query with JOIN instead of N+1 loop
    result = await db.execute(
        select(Booking, TimeSlot)
        .outerjoin(TimeSlot, Booking.slot_id == TimeSlot.id)
        .where(*_preds)
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    rows = result.all()

    try:
        from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

        _pids = {b.provider_id for b, _ in rows if b.provider_id}
        _uids = {b.unit_id for b, _ in rows if b.unit_id}
        _pmap: dict = {}
        _umap: dict = {}
        if _pids:
            for _p in (
                await db.execute(select(_PV).where(_PV.id.in_(_pids)))
            ).scalars().all():
                _pmap[_p.id] = _p.full_name
        if _uids:
            for _u in (
                await db.execute(select(_SU).where(_SU.id.in_(_uids)))
            ).scalars().all():
                _umap[_u.id] = _u.name
    except Exception:
        _pmap = {}
        _umap = {}

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
                unit_id=str(b.unit_id) if b.unit_id else None,
                unit_name=_umap.get(b.unit_id) if b.unit_id else None,
                provider_id=str(b.provider_id) if b.provider_id else None,
                provider_name=_pmap.get(b.provider_id) if b.provider_id else None,
            )
        )
    return items


@router.get("/search", response_model=GlobalSearchResponse)
async def global_search(
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(default=5, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unified org-scoped search across bookings and conversations."""
    like = f"%{q}%"

    bookings_result = await db.execute(
        select(Booking, TimeSlot)
        .outerjoin(TimeSlot, Booking.slot_id == TimeSlot.id)
        .where(
            Booking.org_id == user.org_id,
            or_(
                Booking.customer_name.ilike(like),
                Booking.customer_email.ilike(like),
                Booking.customer_phone.ilike(like),
            ),
        )
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )

    conversations_result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.org_id == user.org_id,
            ChatSession.session_status != "ARCHIVED",
            ChatSession.archived_at.is_(None),
            or_(
                ChatSession.current_intent.ilike(like),
                ChatSession.channel.ilike(like),
                ChatSession.id.cast(String).ilike(like),
                ChatSession.state_data["name"].astext.ilike(like),
                ChatSession.state_data["email"].astext.ilike(like),
                ChatSession.state_data["phone"].astext.ilike(like),
            ),
        )
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )

    return GlobalSearchResponse(
        bookings=[
            GlobalSearchBooking(
                id=str(b.id),
                customer_name=b.customer_name or "Unknown",
                customer_email=b.customer_email or "",
                customer_phone=b.customer_phone,
                slot_start=slot.start_time if slot else None,
                created_at=b.created_at,
            )
            for b, slot in bookings_result.all()
        ],
        conversations=[
            GlobalSearchConversation(
                id=str(s.id),
                session_status=s.session_status or "ACTIVE",
                channel=s.channel or "web",
                customer_name=_clean_customer_value((s.state_data or {}).get("name")),
                customer_email=_clean_customer_value((s.state_data or {}).get("email")),
                customer_phone=_clean_customer_value((s.state_data or {}).get("phone")),
                updated_at=s.updated_at,
            )
            for s in conversations_result.scalars().all()
        ],
    )


@router.get("/conversations")
async def get_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    include_archived: bool = Query(default=False),
    search: Optional[str] = Query(None, max_length=100),
    status: Optional[List[str]] = Query(None),
    channel: Optional[List[str]] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    sort_by: str = Query("updated_at", pattern="^(created_at|updated_at|session_status|current_intent|channel)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    unit_id: Optional[str] = Query(default=None, description="Filter by department"),
    provider_id: Optional[str] = Query(default=None, description="Filter by team member"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List chat sessions for the current org (paginated).

    Staff-scoped: doctor logins see only their own chats, dept managers
    only their units. Unscoped users (owners, individuals) see everything.
    Explicit unit_id/provider_id narrow further (same pattern as /bookings).
    """
    from datetime import timedelta

    predicates = [ChatSession.org_id == user.org_id]
    _sp, _su = await _member_scope(db, user)
    if _sp is not None:
        predicates.append(ChatSession.provider_id == _sp)
    elif _su:
        predicates.append(ChatSession.unit_id.in_(_su))
    if unit_id:
        try:
            import uuid as _uuid

            predicates.append(ChatSession.unit_id == _uuid.UUID(str(unit_id)))
        except Exception:
            pass
    if provider_id:
        try:
            import uuid as _uuid2

            predicates.append(ChatSession.provider_id == _uuid2.UUID(str(provider_id)))
        except Exception:
            pass
    if not include_archived:
        predicates.append(ChatSession.session_status != "ARCHIVED")
        predicates.append(ChatSession.archived_at.is_(None))

    if search:
        like = f"%{search}%"
        predicates.append(
            or_(
                ChatSession.current_intent.ilike(f"%{search}%"),
                ChatSession.channel.ilike(f"%{search}%"),
                ChatSession.id.cast(String).ilike(f"%{search}%"),
                ChatSession.state_data["name"].astext.ilike(like),
                ChatSession.state_data["email"].astext.ilike(like),
                ChatSession.state_data["phone"].astext.ilike(like),
            )
        )
    if status:
        predicates.append(ChatSession.session_status.in_(status))
    if channel:
        predicates.append(ChatSession.channel.in_(channel))
    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="from_date must be YYYY-MM-DD")
        predicates.append(ChatSession.created_at >= fd)
    if to_date:
        try:
            td = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="to_date must be YYYY-MM-DD")
        predicates.append(ChatSession.created_at < td)

    total = (
        await db.execute(select(sa_func.count()).select_from(ChatSession).where(*predicates))
    ).scalar_one()

    allowed_sort = {"created_at", "updated_at", "session_status", "current_intent", "channel"}
    sort_col = sort_by if sort_by in allowed_sort else "updated_at"
    sort_dir = desc if sort_order == "desc" else asc
    sort_column = getattr(ChatSession, sort_col)

    result = await db.execute(
        select(ChatSession)
        .where(*predicates)
        .order_by(sort_column.desc() if sort_dir == desc else sort_column.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    sessions = result.scalars().all()

    try:
        from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

        _pids = {s.provider_id for s in sessions if getattr(s, "provider_id", None)}
        _uids = {s.unit_id for s in sessions if getattr(s, "unit_id", None)}
        _pmap: dict = {}
        _umap: dict = {}
        if _pids:
            for _p in (
                await db.execute(select(_PV).where(_PV.id.in_(_pids)))
            ).scalars().all():
                _pmap[_p.id] = _p.full_name
        if _uids:
            for _u in (
                await db.execute(select(_SU).where(_SU.id.in_(_uids)))
            ).scalars().all():
                _umap[_u.id] = _u.name
    except Exception:
        _pmap = {}
        _umap = {}

    return {
        "items": [
            ConversationItem(
                id=str(s.id),
                session_status=s.session_status or "ACTIVE",
                current_intent=s.current_intent,
                collected_data=s.state_data or {},
                missing_fields=s.missing_fields or [],
                created_at=s.created_at,
                updated_at=s.updated_at,
                channel=s.channel or "web",
                customer_name=_clean_customer_value((s.state_data or {}).get("name")),
                customer_email=_clean_customer_value((s.state_data or {}).get("email")),
                customer_phone=_clean_customer_value((s.state_data or {}).get("phone")),
                unit_id=str(s.unit_id) if getattr(s, "unit_id", None) else None,
                unit_name=_umap.get(s.unit_id) if getattr(s, "unit_id", None) else (s.state_data or {}).get("unit_name"),
                provider_id=str(s.provider_id) if getattr(s, "provider_id", None) else None,
                provider_name=_pmap.get(s.provider_id) if getattr(s, "provider_id", None) else (s.state_data or {}).get("provider_name"),
            )
            for s in sessions
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/bookings", response_model=BookingPageResponse)
async def list_bookings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    status: Optional[str] = Query(None, pattern="^(CONFIRMED|CANCELLED|ACTIVE|ARCHIVED|EXPIRED)$"),
    attendance_status: Optional[str] = Query(None, pattern="^(SCHEDULED|ATTENDED|NO_SHOW|CANCELLED|RESCHEDULED)$"),
    meeting_mode: Optional[str] = Query(None, pattern="^(ONLINE|OFFLINE)$"),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    unit_id: Optional[str] = Query(default=None, description="Filter by department"),
    provider_id: Optional[str] = Query(default=None, description="Filter by doctor"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paginated bookings list for the current org (search + date range + status + attendance + mode + dept/doctor). Members see only own provider bookings."""
    from datetime import timedelta

    predicates = [Booking.org_id == user.org_id]
    # Member own-dashboard scoping: providers.user_id → only own bookings
    _mpid = await _member_provider_id(db, user)
    if _mpid is not None:
        predicates.append(Booking.provider_id == _mpid)
    if unit_id:
        try:
            import uuid as _uuid

            predicates.append(Booking.unit_id == _uuid.UUID(str(unit_id)))
        except Exception:
            pass
    if provider_id:
        try:
            import uuid as _uuid2

            predicates.append(Booking.provider_id == _uuid2.UUID(str(provider_id)))
        except Exception:
            pass
    if search:
        like = f"%{search}%"
        # Customer fields + doctor/team-member name + department name, all
        # org-scoped. Names resolve via EXISTS so individual orgs (no catalog
        # rows) behave exactly as before.
        from app.models.service_unit import Provider as _SP, ServiceUnit as _SU

        predicates.append(
            or_(
                Booking.customer_name.ilike(like),
                Booking.customer_email.ilike(like),
                Booking.customer_phone.ilike(like),
                select(1)
                .select_from(_SP)
                .where(
                    _SP.id == Booking.provider_id,
                    _SP.org_id == user.org_id,
                    _SP.full_name.ilike(like),
                )
                .exists(),
                select(1)
                .select_from(_SU)
                .where(
                    _SU.id == Booking.unit_id,
                    _SU.org_id == user.org_id,
                    _SU.name.ilike(like),
                )
                .exists(),
            )
        )
    if status:
        predicates.append(ChatSession.session_status == status)
    if attendance_status:
        predicates.append(Booking.attendance_status == attendance_status)
    if meeting_mode:
        predicates.append(Booking.meeting_type_snapshot == meeting_mode)
    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="from_date must be YYYY-MM-DD")
        predicates.append(Booking.created_at >= fd)
    if to_date:
        try:
            td = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="to_date must be YYYY-MM-DD")
        predicates.append(Booking.created_at < td)

    total = (
        await db.execute(select(sa_func.count()).select_from(Booking).where(*predicates))
    ).scalar_one()

    result = await db.execute(
        select(Booking, TimeSlot, ChatSession)
        .outerjoin(TimeSlot, Booking.slot_id == TimeSlot.id)
        .outerjoin(ChatSession, Booking.session_id == ChatSession.id)
        .where(*predicates)
        .order_by(Booking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    _rows = result.all()

    try:
        from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

        _pids = {b.provider_id for b, _, _ in _rows if b.provider_id}
        _uids = {b.unit_id for b, _, _ in _rows if b.unit_id}
        _pmap: dict = {}
        _umap: dict = {}
        if _pids:
            for _p in (
                await db.execute(select(_PV).where(_PV.id.in_(_pids)))
            ).scalars().all():
                _pmap[_p.id] = _p.full_name
        if _uids:
            for _u in (
                await db.execute(select(_SU).where(_SU.id.in_(_uids)))
            ).scalars().all():
                _umap[_u.id] = _u.name
    except Exception:
        _pmap = {}
        _umap = {}

    items = []
    for b, slot, s in _rows:
        _mls = b.meeting_link_status
        _mls_val = _mls.value if _mls is not None and hasattr(_mls, "value") else (str(_mls) if _mls is not None else None)
        _mlp = b.meeting_link_provider
        _mlp_val = _mlp.value if _mlp is not None and hasattr(_mlp, "value") else (str(_mlp) if _mlp is not None else None)
        items.append(
            BookingPageItem(
                id=str(b.id),
                customer_name=b.customer_name or "Unknown",
                customer_email=b.customer_email or "",
                customer_phone=b.customer_phone,
                slot_start=slot.start_time if slot else None,
                slot_end=slot.end_time if slot else None,
                created_at=b.created_at,
                session_id=str(s.id) if s else None,
                session_status=s.session_status if s else None,
                meeting_type_snapshot=b.meeting_type_snapshot,
                meeting_link_status=_mls_val,
                meeting_link_url=b.meeting_link_url,
                meeting_link_provider=_mlp_val,
                attendance_status=b.attendance_status,
                attended_at=b.attended_at,
                attendance_notes=b.attendance_notes,
                cancellation_reason=b.cancellation_reason,
                unit_id=str(b.unit_id) if b.unit_id else None,
                unit_name=_umap.get(b.unit_id) if b.unit_id else None,
                provider_id=str(b.provider_id) if b.provider_id else None,
                provider_name=_pmap.get(b.provider_id) if b.provider_id else None,
            )
        )
    return BookingPageResponse(items=items, total=total, page=page, page_size=page_size)


# ── Attendance (online vs offline differentiated) ──────────────────────────


class AttendanceUpdateRequest(BaseModel):
    attendance_status: str = Field(..., pattern="^(SCHEDULED|ATTENDED|NO_SHOW|CANCELLED|RESCHEDULED)$")
    attendance_notes: Optional[str] = Field(None, max_length=500)
    cancellation_reason: Optional[str] = Field(None, max_length=500)


class AttendanceResponse(BaseModel):
    id: str
    attendance_status: str
    attended_at: Optional[datetime] = None
    attendance_notes: Optional[str] = None
    cancellation_reason: Optional[str] = None
    meeting_type_snapshot: Optional[str] = None


@router.put("/bookings/{booking_id}/attendance", response_model=AttendanceResponse)
async def update_booking_attendance(
    booking_id: str,
    payload: AttendanceUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark attendance. Online vs offline handled differently in UI but same endpoint.
    - ONLINE ATTENDED: expects meeting_link_status READY, optionally auto-verified via Join click.
    - OFFLINE ATTENDED: manual after appointment time, no link needed.
    - NO_SHOW / CANCELLED / RESCHEDULED for both.
    """
    import uuid as _uuid

    try:
        bid = _uuid.UUID(booking_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid booking ID")

    result = await db.execute(select(Booking).where(Booking.id == bid, Booking.org_id == user.org_id))
    booking = result.scalars().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Validate online vs offline rules
    is_online = (booking.meeting_type_snapshot or "OFFLINE") == "ONLINE"
    if payload.attendance_status == "ATTENDED" and is_online:
        # For online, ensure link was READY or at least not missing - allow manual override with note
        if booking.meeting_link_status not in (None, "READY", "UNAVAILABLE", "NOT_REQUIRED"):
            pass  # allow
    # Update
    booking.attendance_status = payload.attendance_status
    booking.attendance_notes = payload.attendance_notes
    booking.cancellation_reason = payload.cancellation_reason
    if payload.attendance_status == "ATTENDED":
        booking.attended_at = datetime.now(timezone.utc)
        booking.attendance_marked_by = user.id
    elif payload.attendance_status in ("CANCELLED", "NO_SHOW"):
        booking.attended_at = None
        booking.attendance_marked_by = user.id
    elif payload.attendance_status == "RESCHEDULED":
        booking.attended_at = None
        booking.attendance_marked_by = user.id
    else:  # SCHEDULED
        booking.attended_at = None
        booking.attendance_marked_by = None

    # Free the slot for the next patient when the booking is cancelled or
    # moved: the TimeSlot row is released (not deleted, so booking history
    # stays) and the lock engine reuses it for the next booking attempt.
    if payload.attendance_status in ("CANCELLED", "RESCHEDULED"):
        try:
            from app.models.booking import TimeSlot as _TS

            _slot = (
                await db.execute(
                    select(_TS).where(
                        _TS.id == booking.slot_id, _TS.org_id == booking.org_id
                    )
                )
            ).scalars().first()
            if _slot is not None:
                _slot.is_booked = False
                _slot.locked_until = None
        except Exception:
            pass

    await db.commit()
    await db.refresh(booking)
    return AttendanceResponse(
        id=str(booking.id),
        attendance_status=booking.attendance_status,
        attended_at=booking.attended_at,
        attendance_notes=booking.attendance_notes,
        cancellation_reason=booking.cancellation_reason,
        meeting_type_snapshot=booking.meeting_type_snapshot,
    )


@router.post("/bookings/{booking_id}/reschedule", response_model=AttendanceResponse)
async def reschedule_booking_attendance(
    booking_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reschedule: mark old as RESCHEDULED and create a placeholder for new slot.
    For ONLINE, new Meet link will be generated on next booking creation; for OFFLINE, just new slot.
    This is a lightweight attendance-level reschedule marker - the actual new booking is created via widget.
    """
    import uuid as _uuid

    try:
        bid = _uuid.UUID(booking_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid booking ID")
    result = await db.execute(select(Booking).where(Booking.id == bid, Booking.org_id == user.org_id))
    booking = result.scalars().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking.attendance_status = "RESCHEDULED"
    booking.attendance_marked_by = user.id
    booking.attendance_notes = payload.get("notes") if isinstance(payload, dict) else None
    # Free the old slot for the next patient (same rule as attendance cancel).
    try:
        from app.models.booking import TimeSlot as _TS2

        _slot2 = (
            await db.execute(
                select(_TS2).where(
                    _TS2.id == booking.slot_id, _TS2.org_id == booking.org_id
                )
            )
        ).scalars().first()
        if _slot2 is not None:
            _slot2.is_booked = False
            _slot2.locked_until = None
    except Exception:
        pass
    await db.commit()
    await db.refresh(booking)
    return AttendanceResponse(
        id=str(booking.id),
        attendance_status=booking.attendance_status,
        attended_at=booking.attended_at,
        attendance_notes=booking.attendance_notes,
        cancellation_reason=booking.cancellation_reason,
        meeting_type_snapshot=booking.meeting_type_snapshot,
    )


@router.get("/bookings/cancelled", response_model=CancelledBookingPageResponse)
async def list_cancelled_bookings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    unit_id: Optional[str] = Query(default=None, description="Filter by department"),
    provider_id: Optional[str] = Query(default=None, description="Filter by team member"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paginated cancelled bookings = CANCELLED chat sessions for the org."""
    from datetime import timedelta

    predicates = [
        ChatSession.org_id == user.org_id,
        ChatSession.session_status == "CANCELLED",
    ]
    # Staff scope (linked logins see only own) + explicit dept/member filters.
    _sp, _su = await _member_scope(db, user)
    _eff_provider = provider_id or (_sp and str(_sp))
    if _eff_provider:
        try:
            import uuid as _uuid

            predicates.append(ChatSession.provider_id == _uuid.UUID(str(_eff_provider)))
        except Exception:
            pass
    if unit_id:
        try:
            import uuid as _uuid2

            predicates.append(ChatSession.unit_id == _uuid2.UUID(str(unit_id)))
        except Exception:
            pass
    elif _su:
        try:
            import uuid as _uuid3

            predicates.append(
                ChatSession.unit_id.in_([_uuid3.UUID(str(x)) for x in _su])
            )
        except Exception:
            pass
    if search:
        like = f"%{search}%"
        predicates.append(
            or_(
                ChatSession.state_data["name"].astext.ilike(like),
                ChatSession.state_data["customer_name"].astext.ilike(like),
                ChatSession.state_data["email"].astext.ilike(like),
                ChatSession.state_data["customer_email"].astext.ilike(like),
                ChatSession.state_data["phone"].astext.ilike(like),
            )
        )
    if from_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="from_date must be YYYY-MM-DD")
        predicates.append(ChatSession.updated_at >= fd)
    if to_date:
        try:
            td = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="to_date must be YYYY-MM-DD")
        predicates.append(ChatSession.updated_at < td)

    # Fetch all matching CANCELLED sessions (latest first), then dedupe to
    # one row per appointment. Book + cancel via lookup (web widget or
    # WhatsApp) can touch two sessions (original booking session +
    # cancel-lookup session) for the same appointment, so without this the
    # page shows duplicates.
    result = await db.execute(
        select(ChatSession)
        .where(*predicates)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = result.scalars().all()

    def _dedupe_key(data: dict, session_id: str) -> str:
        email = str(data.get("email") or data.get("customer_email") or "").strip().lower()
        phone_digits = "".join(
            c for c in str(data.get("phone") or "") if c.isdigit()
        )[-10:]
        date = str(data.get("date") or "").strip()
        if email and date:
            return f"email:{email}|date:{date}"
        if phone_digits and date:
            return f"phone:{phone_digits}|date:{date}"
        if email:
            return f"email:{email}"
        if phone_digits:
            return f"phone:{phone_digits}"
        return f"session:{session_id}"

    try:
        from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

        _cpids = {s.provider_id for s in sessions if getattr(s, "provider_id", None)}
        _cuids = {s.unit_id for s in sessions if getattr(s, "unit_id", None)}
        _cpmap: dict = {}
        _cumap: dict = {}
        if _cpids:
            for _p in (
                await db.execute(select(_PV).where(_PV.id.in_(_cpids)))
            ).scalars().all():
                _cpmap[_p.id] = _p.full_name
        if _cuids:
            for _u in (
                await db.execute(select(_SU).where(_SU.id.in_(_cuids)))
            ).scalars().all():
                _cumap[_u.id] = _u.name
    except Exception:
        _cpmap = {}
        _cumap = {}

    seen: set = set()
    items = []
    for s in sessions:
        data = s.state_data or {}
        # Prefer the stored cancelled booking id for dedupe so the original
        # booking session and its cancel-lookup session collapse even if
        # contact details differ slightly.
        _bid = str(data.get("cancelled_booking_id") or "").strip()
        _name = data.get("name") or data.get("customer_name") or ""
        _email = data.get("email") or data.get("customer_email") or ""
        # Skip aborted booking *requests* (legacy pre-booking CANCEL intent):
        # no real booking was ever deleted, so no name/email/booking_id.
        # Real cancellations always carry a name, an email, or a booking id.
        if not _bid and not str(_name).strip() and not str(_email).strip():
            continue
        key = f"booking:{_bid}" if _bid else _dedupe_key(data, str(s.id))
        if key in seen:
            continue
        seen.add(key)
        items.append(
            CancelledBookingItem(
                id=str(s.id),
                customer_name=str(_name).strip() or "Unknown",
                customer_email=str(_email).strip(),
                customer_phone=data.get("phone") or None,
                booking_date=data.get("date") or None,
                booking_time=data.get("time") or None,
                cancelled_at=s.updated_at,
                booking_id=_bid or None,
                unit_id=str(s.unit_id) if getattr(s, "unit_id", None) else None,
                unit_name=_cumap.get(s.unit_id) if getattr(s, "unit_id", None) else None,
                provider_id=str(s.provider_id) if getattr(s, "provider_id", None) else None,
                provider_name=_cpmap.get(s.provider_id) if getattr(s, "provider_id", None) else None,
            )
        )
    total = len(items)
    paged = items[(page - 1) * page_size : page * page_size]
    return CancelledBookingPageResponse(
        items=paged, total=total, page=page, page_size=page_size
    )


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
        logo_url=getattr(config, "logo_url", None),
        org_name=config.org_name if getattr(config, "org_name", None) else (org.name if org else "Unknown"),
        business_name=getattr(config, "business_name", None),
        api_key=masked_api_key,
        require_otp=config.require_otp if hasattr(config, "require_otp") else True,
        require_phone_otp=getattr(config, "require_phone_otp", False),
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
        "require_phone_otp": getattr(config, "require_phone_otp", False),
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
    if update.require_phone_otp is not None:
        config.require_phone_otp = update.require_phone_otp
    if update.max_booking_days_ahead is not None:
        config.max_booking_days_ahead = update.max_booking_days_ahead
        # Bidirectional single-source sync: agent-config writes mirror into
        # BusinessSchedule (the availability truth) + expire stale pickers.
        try:
            _sched_res = await db.execute(
                select(BusinessSchedule).where(
                    BusinessSchedule.org_id == user.org_id
                )
            )
            _sched = _sched_res.scalars().first()
            if _sched is None:
                _sched = BusinessSchedule(org_id=user.org_id)
                db.add(_sched)
            _sched.booking_window_days = update.max_booking_days_ahead
            from app.engine.booking_directive.service import (
                DirectiveType as _DTExp2,
            )
            from app.engine.booking_directive.service import (
                expire_pending_by_type as _expire_dirs2,
            )

            await _expire_dirs2(
                db, user.org_id, [_DTExp2.SELECT_DATE, _DTExp2.SELECT_SLOT]
            )
        except Exception:
            pass
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
        # Offline mode cannot have an online provider — force NONE
        if update.meeting_mode == MeetingMode.OFFLINE_ONLY:
            config.meeting_provider_default = MeetingProviderDefault.NONE
            # Also clear active provider if it was online
            if getattr(config, "active_meeting_provider", None) not in (None, ActiveMeetingProvider.NONE):
                config.active_meeting_provider = ActiveMeetingProvider.NONE
    if update.allow_hybrid_selection is not None:
        config.allow_hybrid_selection = update.allow_hybrid_selection
        config.meeting_policy_updated_at = datetime.now(timezone.utc)
        config.meeting_policy_updated_by = user.email
    if update.meeting_provider_default is not None:
        # Ignore provider selection when offline
        if config.meeting_mode == MeetingMode.OFFLINE_ONLY:
            config.meeting_provider_default = MeetingProviderDefault.NONE
        else:
            config.meeting_provider_default = update.meeting_provider_default
        config.meeting_policy_updated_at = datetime.now(timezone.utc)
        config.meeting_policy_updated_by = user.email
    if update.org_name is not None:
        config.org_name = update.org_name
    if update.business_name is not None:
        config.business_name = update.business_name
    # Anti-drift: the business name is stored twice (column + nested
    # custom_prompts.business_name) and they disagreed in production, so
    # reconcile on every save. Column is authoritative.
    try:
        from app.engine.business_identity import (
            reconcile_business_names as _reconcile_bn,
        )

        _cp = (
            config.custom_prompts
            if isinstance(config.custom_prompts, dict)
            else {}
        )
        _col, _nested = _reconcile_bn(
            getattr(config, "business_name", ""),
            _cp.get("business_name", ""),
        )
        if _col != getattr(config, "business_name", ""):
            config.business_name = _col or None
        if _nested != _cp.get("business_name", ""):
            _cp = dict(_cp)
            _cp["business_name"] = _nested
            config.custom_prompts = _cp if _cp else None
    except Exception:
        pass
    if update.logo_url is not None:
        config.logo_url = update.logo_url

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
            "require_phone_otp": getattr(config, "require_phone_otp", False),
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
        logo_url=getattr(config, "logo_url", None),
        org_name=org.name if org else "Unknown",
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


# ── Logo upload endpoints ───────────────────────────────────────────────
LOGO_DIR = Path("/app/uploads/logos")
LOGO_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_LOGO_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
ALLOWED_LOGO_MIME = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB


@router.post("/agent/logo", response_model=dict)
async def upload_agent_logo(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload chatbot logo for the current org (left of Brand Color)."""
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_LOGO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported image type {ext}. Allowed: png, jpg, jpeg, webp, svg")
    if file.content_type and file.content_type not in ALLOWED_LOGO_MIME and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Unsupported MIME {file.content_type}")
    raw = await file.read()
    if not raw or len(raw) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="Logo exceeds 2MB limit")
    # Basic SVG XSS check — reject script tags
    if ext == ".svg" and b"<script" in raw.lower():
        raise HTTPException(status_code=400, detail="SVG contains disallowed content")
    # Store
    filename = f"{user.org_id}{ext}"
    dest = LOGO_DIR / filename
    # Remove old logo files for this org with different ext
    for p in LOGO_DIR.glob(f"{user.org_id}.*"):
        if p != dest:
            try:
                p.unlink()
            except Exception:
                pass
    dest.write_bytes(raw)
    # Determine MIME for serving
    mime = file.content_type or ("image/svg+xml" if ext == ".svg" else f"image/{ext.lstrip('.')}")
    # Update OrgConfig
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="Org config not found")
    # Public URL for widget/admin — served via public endpoint below
    public_url = f"/api/v1/dashboard/public/logo/{user.org_id}"
    config.logo_url = public_url
    await db.commit()
    return {"logo_url": public_url, "filename": filename, "mime": mime}


@router.delete("/agent/logo", response_model=dict)
async def delete_agent_logo(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove chatbot logo."""
    for p in LOGO_DIR.glob(f"{user.org_id}.*"):
        try:
            p.unlink()
        except Exception:
            pass
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    config = result.scalars().first()
    if config:
        config.logo_url = None
        await db.commit()
    return {"success": True}


@router.get("/agent/logo")
async def get_agent_logo(
    user: User = Depends(get_current_user),
):
    """Serve current org's logo for authenticated admin panel preview."""
    for p in LOGO_DIR.glob(f"{user.org_id}.*"):
        mime = "image/svg+xml" if p.suffix == ".svg" else f"image/{p.suffix.lstrip('.')}"
        if mime == "image/jpg":
            mime = "image/jpeg"
        from fastapi.responses import FileResponse
        return FileResponse(str(p), media_type=mime)
    raise HTTPException(status_code=404, detail="Logo not found")


@router.get("/public/logo/{org_id}")
async def get_public_logo(org_id: str):
    """Public logo for widget — no auth, fetched by chatbot embed."""
    try:
        uuid_obj = _uuid.UUID(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid org id")
    for p in LOGO_DIR.glob(f"{uuid_obj}.*"):
        mime = "image/svg+xml" if p.suffix == ".svg" else f"image/{p.suffix.lstrip('.')}"
        if mime == "image/jpg":
            mime = "image/jpeg"
        from fastapi.responses import FileResponse
        return FileResponse(str(p), media_type=mime, headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(status_code=404, detail="Logo not found")


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
        try:
            _cs_data = dict(chat_session.state_data or {})
            _cs_data["cancelled_booking_id"] = str(booking.id)
            chat_session.state_data = _cs_data
            from sqlalchemy.orm.attributes import flag_modified as _flag_mod2

            _flag_mod2(chat_session, "state_data")
        except Exception:
            pass
        await db.commit()

    from app.core.org_notify import notify_org

    await notify_org(
        org_id=user.org_id,
        kind="booking_cancelled",
        title="Appointment cancelled",
        body=(
            f"Booking for {booking.customer_name or 'a customer'} "
            f"({old_values.get('date') or 'unknown date'} {old_values.get('time') or ''}) was cancelled by an admin.".strip()
        ),
        resource_type="booking",
        resource_id=str(booking.id),
    )

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

    try:
        session_data = (chat_session.state_data or {}) if chat_session else {}
        customer_email = session_data.get("email") or booking.customer_email
        if customer_email:
            channel_name = getattr(chat_session, "channel", None) or "web"
            booking_details = {
                "booking_id": str(booking.id),
                "name": session_data.get("name") or booking.customer_name,
                "email": customer_email,
                "date": session_data.get("date"),
                "time": session_data.get("time"),
                "source_channel": {
                    "web": "Web Widget",
                    "whatsapp": "WhatsApp",
                    "whatsapp_cloud": "WhatsApp",
                }.get(channel_name, "Web Widget"),
            }

            if booking.meeting_type_snapshot:
                booking_details["meeting_type_snapshot"] = booking.meeting_type_snapshot
            if booking.meeting_link_status:
                booking_details["meeting_link_status"] = booking.meeting_link_status.value
            if booking.meeting_link_url:
                booking_details["meeting_link_url"] = booking.meeting_link_url
            if booking.meeting_link_provider:
                booking_details["meeting_link_provider"] = booking.meeting_link_provider.value
            if booking.user_timezone:
                booking_details["user_timezone"] = booking.user_timezone
            if booking.org_timezone_snapshot:
                booking_details["org_timezone_snapshot"] = booking.org_timezone_snapshot

            await send_cancellation_email(
                customer_email=customer_email,
                customer_name=session_data.get("name") or booking.customer_name or "there",
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
#  CONVERSATION TRANSCRIPT
# ══════════════════════════════════════════════════


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    sender_type: Optional[str] = None
    channel: Optional[str] = None
    direction: Optional[str] = None
    send_status: Optional[str] = None
    provider_message_id: Optional[str] = None
    external_message_id: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    error_message: Optional[str] = None


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
            sender_type=getattr(m, "sender_type", None),
            channel=getattr(m, "channel", None),
            direction=getattr(m, "direction", None),
            send_status=getattr(m, "send_status", None),
            provider_message_id=getattr(m, "provider_message_id", None),
            external_message_id=getattr(m, "external_message_id", None),
            metadata=m.metadata_,
            created_at=m.created_at,
            sent_at=getattr(m, "sent_at", None),
            delivered_at=getattr(m, "delivered_at", None),
            read_at=getattr(m, "read_at", None),
            failed_at=getattr(m, "failed_at", None),
            error_message=getattr(m, "error_message", None),
        )
        for m in messages
    ]


# ── WhatsApp Integration Config ──────────────────────────────
# NOTE: Meta Cloud API config removed (QR/Baileys transport is the only
# WhatsApp channel now). Pairing lives in endpoints/whatsapp_qr.py and
# connection state in whatsapp_connections.


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
    request: Request,
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

    old_masked = (config.widget_token[:8] + "..." + config.widget_token[-4:]) if config.widget_token else "none"
    logger.info(f"Widget token regenerate for org {user.org_id} user {user.email} old {old_masked} ip {request.client.host if request.client else ''}")
    config.widget_token = _generate_widget_token()
    await db.commit()
    await db.refresh(config)

    masked = config.widget_token[:8] + "..." + config.widget_token[-4:]
    logger.info(f"Widget token regenerated for org {user.org_id} new {masked}")
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
    open_time: Optional[str] = None  # "HH:MM" — null for closed days (proper nullable)
    close_time: Optional[str] = None  # "HH:MM" — null for closed days
    break_start: Optional[str] = None
    break_end: Optional[str] = None


class ScheduleResponse(BaseModel):
    slot_duration_minutes: int
    booking_window_days: int
    timezone: str
    days: List[ScheduleDayItem]


class ScheduleUpdate(BaseModel):
    slot_duration_minutes: Optional[int] = None
    # A year has 365 days (366 only when the window can include Feb 29 of a
    # leap year) — enforced dynamically, not by a static cap.
    booking_window_days: Optional[int] = Field(default=None, ge=1, le=366)
    timezone: Optional[str] = None
    days: Optional[List[ScheduleDayItem]] = None

    @field_validator("booking_window_days")
    @classmethod
    def _leap_aware_horizon(cls, value):
        return _check_horizon_cap(value, "booking_window_days")


def _check_horizon_cap(value, field_name: str = "booking_window_days"):
    """Leap-aware horizon ceiling shared by all write schemas."""
    if value is None:
        return None
    from app.engine.scheduling_policy import max_horizon_days

    cap = max_horizon_days()
    if not (1 <= int(value) <= cap):
        raise ValueError(
            f"{field_name} must be between 1 and {cap} days "
            f"(366 only when the booking window can include Feb 29 of a leap year)"
        )
    return value


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

    # Org template only: per-doctor overrides (provider_id set) live in
    # Departments → Hours and must never leak into the hospital default view.
    days_result = await db.execute(
        select(ScheduleDay)
        .where(
            ScheduleDay.org_id == user.org_id,
            ScheduleDay.provider_id.is_(None),
        )
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

    # Upsert org-template ScheduleDay rows only (never touch doctor overrides)
    if payload.days is not None:
        for day_data in payload.days:
            day_result = await db.execute(
                select(ScheduleDay).where(
                    ScheduleDay.org_id == user.org_id,
                    ScheduleDay.day_of_week == day_data.day_of_week,
                    ScheduleDay.provider_id.is_(None),
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

    # Hours/horizon changed: stored date/time prompts go stale instantly.
    # Expire pending picker directives so the next turn regenerates them
    # from the new schedule instead of showing yesterday's window.
    if payload.booking_window_days is not None or payload.days is not None:
        try:
            from app.engine.booking_directive.service import (
                DirectiveType as _DTExp,
            )
            from app.engine.booking_directive.service import (
                expire_pending_by_type as _expire_dirs,
            )

            await _expire_dirs(db, user.org_id, [_DTExp.SELECT_DATE, _DTExp.SELECT_SLOT])
            await db.commit()
        except Exception:
            pass

    days_result = await db.execute(
        select(ScheduleDay)
        .where(
            ScheduleDay.org_id == user.org_id,
            ScheduleDay.provider_id.is_(None),
        )
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

    # Org-wide exceptions only (doctor-specific live in Departments → Hours)
    q = select(ScheduleException).where(
        ScheduleException.org_id == user.org_id,
        ScheduleException.provider_id.is_(None),
    )
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

    # Delete existing org-wide exception for same date (upsert).
    # Doctor-specific rows are untouched.
    existing_result = await db.execute(
        select(ScheduleException).where(
            ScheduleException.org_id == user.org_id,
            ScheduleException.exception_date == exc_date,
            ScheduleException.provider_id.is_(None),
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
    unit_id: Optional[str] = Query(default=None, description="Filter by department"),
    provider_id: Optional[str] = Query(default=None, description="Filter by doctor"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return all confirmed bookings in a date range (inclusive) for the calendar grid.
    Joins TimeSlot for slot times and ChatSession for collected form data.
    Org-mode: optional unit_id/provider_id filters + RBAC scope (doctor sees own).
    """
    from datetime import date as dt_date

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
        )

    # RBAC scope: doctor login sees only own bookings unless explicit filter
    _scope_provider = await _member_provider_id(db, user)
    _eff_provider = provider_id or _scope_provider
    try:
        from app.core.org_mode import get_managed_unit_ids as _managed_units

        _scope_units = await _managed_units(db, user.id)
    except Exception:
        _scope_units = []

    # Join Booking → TimeSlot to filter by date range
    _preds = [
        Booking.org_id == user.org_id,
        TimeSlot.start_time >= start_dt,
        TimeSlot.start_time <= end_dt,
    ]
    if _eff_provider:
        try:
            import uuid as _uuid

            _preds.append(Booking.provider_id == _uuid.UUID(str(_eff_provider)))
        except Exception:
            pass
    if unit_id:
        try:
            import uuid as _uuid2

            _preds.append(Booking.unit_id == _uuid2.UUID(str(unit_id)))
        except Exception:
            pass
    elif _scope_units:
        try:
            import uuid as _uuid3

            _preds.append(
                Booking.unit_id.in_([_uuid3.UUID(str(x)) for x in _scope_units])
            )
        except Exception:
            pass
    stmt = (
        select(Booking, TimeSlot, ChatSession)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .outerjoin(ChatSession, Booking.session_id == ChatSession.id)
        .where(*_preds)
        .order_by(TimeSlot.start_time.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    # Batch names for dept/doctor badges (no N+1)
    try:
        from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

        _pids = {b.provider_id for b, _, _ in rows if b.provider_id}
        _uids = {b.unit_id for b, _, _ in rows if b.unit_id}
        _pmap = {}
        _umap = {}
        if _pids:
            for _p in (
                await db.execute(select(_PV).where(_PV.id.in_(_pids)))
            ).scalars().all():
                _pmap[_p.id] = _p.full_name
        if _uids:
            for _u in (
                await db.execute(select(_SU).where(_SU.id.in_(_uids)))
            ).scalars().all():
                _umap[_u.id] = _u.name
    except Exception:
        _pmap = {}
        _umap = {}

    items = []
    _extra_fields = await _calendar_extra_field_names(db, user.org_id)
    for booking, slot, session in rows:
        collected = _filter_collected_data(
            session.state_data if session and session.state_data else {},
            _extra_fields,
        )
        # Normalize enums to plain strings for JSON
        _mls = booking.meeting_link_status
        _mls_val = _mls.value if _mls is not None and hasattr(_mls, "value") else (str(_mls) if _mls is not None else None)
        _mlp = booking.meeting_link_provider
        _mlp_val = _mlp.value if _mlp is not None and hasattr(_mlp, "value") else (str(_mlp) if _mlp is not None else None)
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
                meeting_type_snapshot=booking.meeting_type_snapshot,
                meeting_link_status=_mls_val,
                meeting_link_url=booking.meeting_link_url,
                meeting_link_provider=_mlp_val,
                unit_id=str(booking.unit_id) if booking.unit_id else None,
                unit_name=_umap.get(booking.unit_id) if booking.unit_id else None,
                provider_id=str(booking.provider_id) if booking.provider_id else None,
                provider_name=_pmap.get(booking.provider_id) if booking.provider_id else None,
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

    _sp, _su = await _member_scope(db, user)
    _preds = [Booking.org_id == user.org_id, TimeSlot.start_time >= cutoff]
    if _sp is not None:
        _preds.append(Booking.provider_id == _sp)
    elif _su:
        _preds.append(Booking.unit_id.in_(_su))
    stmt = (
        select(Booking, TimeSlot, ChatSession)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .outerjoin(ChatSession, Booking.session_id == ChatSession.id)
        .where(*_preds)
        .order_by(TimeSlot.start_time.asc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.all()

    try:
        from app.models.service_unit import Provider as _PV, ServiceUnit as _SU

        _pids = {b.provider_id for b, _, _ in rows if b.provider_id}
        _uids = {b.unit_id for b, _, _ in rows if b.unit_id}
        _pmap: dict = {}
        _umap: dict = {}
        if _pids:
            for _p in (
                await db.execute(select(_PV).where(_PV.id.in_(_pids)))
            ).scalars().all():
                _pmap[_p.id] = _p.full_name
        if _uids:
            for _u in (
                await db.execute(select(_SU).where(_SU.id.in_(_uids)))
            ).scalars().all():
                _umap[_u.id] = _u.name
    except Exception:
        _pmap = {}
        _umap = {}

    items = []
    _extra_fields = await _calendar_extra_field_names(db, user.org_id)
    for booking, slot, session in rows:
        collected = _filter_collected_data(
            session.state_data if session and session.state_data else {},
            _extra_fields,
        )
        _mls = booking.meeting_link_status
        _mls_val = _mls.value if _mls is not None and hasattr(_mls, "value") else (str(_mls) if _mls is not None else None)
        _mlp = booking.meeting_link_provider
        _mlp_val = _mlp.value if _mlp is not None and hasattr(_mlp, "value") else (str(_mlp) if _mlp is not None else None)
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
                meeting_type_snapshot=booking.meeting_type_snapshot,
                meeting_link_status=_mls_val,
                meeting_link_url=booking.meeting_link_url,
                meeting_link_provider=_mlp_val,
                unit_id=str(booking.unit_id) if booking.unit_id else None,
                unit_name=_umap.get(booking.unit_id) if booking.unit_id else None,
                provider_id=str(booking.provider_id) if booking.provider_id else None,
                provider_name=_pmap.get(booking.provider_id) if booking.provider_id else None,
            )
        )
    return items


# Onboarding section
# ── Onboarding ──────────────────────────────────────────────────────

class OnboardingBusinessInfo(BaseModel):
    industry_type: str = Field(..., min_length=1, max_length=100)


class OnboardingHoursDay(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)  # 0=Mon, 6=Sun
    is_active: bool = True
    open_time: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    close_time: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    break_start: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    break_end: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class OnboardingHours(BaseModel):
    timezone: str = Field(..., min_length=1)
    slot_duration_minutes: int = Field(..., ge=15, le=120)
    booking_window_days: int = Field(..., ge=1, le=366)
    weekly_schedule: List[OnboardingHoursDay] = Field(..., min_length=7, max_length=7)

    @field_validator("booking_window_days")
    @classmethod
    def _leap_aware_horizon(cls, value):
        return _check_horizon_cap(value, "booking_window_days")


class OnboardingAgent(BaseModel):
    agent_name: str = Field(default="Booking Assistant", max_length=100)
    greeting_message: str = Field(default="Hello! How can I help you book today?", max_length=500)
    tone: str = Field(default="friendly and professional", max_length=50)
    require_otp: bool = True


class OnboardingRequest(BaseModel):
    business: OnboardingBusinessInfo
    hours: OnboardingHours
    agent: OnboardingAgent


class OnboardingResponse(BaseModel):
    success: bool
    message: str


class OnboardingGateResponse(BaseModel):
    onboarding_completed: bool
    industry_type: Optional[str] = None
    hours_configured: bool = False


@router.get("/onboarding/status", response_model=OnboardingGateResponse)
async def get_onboarding_gate(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if onboarding is completed for the current user's org."""
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    hours_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
    )
    schedule = hours_result.scalars().first()

    return OnboardingGateResponse(
        onboarding_completed=org.onboarding_completed,
        industry_type=org.industry_type,
        hours_configured=schedule is not None,
    )


@router.post("/onboarding", response_model=OnboardingResponse)
async def complete_onboarding(
    data: OnboardingRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Complete the mandatory onboarding flow for a new organization."""
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Upsert business info
    org.industry_type = data.business.industry_type
    org.onboarding_completed = True
    # Note: Organization has no timezone column — timezone lives in BusinessSchedule + OrgConfig

    # Upsert OrgConfig so /dashboard/agent and /dashboard/schedule pages auto-fill after onboarding
    config_result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    org_config = config_result.scalars().first()
    if not org_config:
        org_config = OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            field_definitions=[],
            custom_prompts={},
            timezone=data.hours.timezone,
            ui_brand_color="#3B82F6",
            require_otp=data.agent.require_otp,
            max_booking_days_ahead=data.hours.booking_window_days,
        )
        db.add(org_config)
        await db.flush()  # ensure org_config is tracked before updates below
    org_config.timezone = data.hours.timezone

    # Upsert BusinessSchedule
    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
    )
    schedule = schedule_result.scalars().first()
    if not schedule:
        schedule = BusinessSchedule(org_id=org.id)
        db.add(schedule)

    schedule.timezone = data.hours.timezone
    schedule.slot_duration_minutes = data.hours.slot_duration_minutes
    schedule.booking_window_days = data.hours.booking_window_days

    # Upsert org-template ScheduleDays (doctor overrides are preserved)
    # Delete existing template days and recreate
    await db.execute(
        ScheduleDay.__table__.delete().where(
            ScheduleDay.org_id == org.id,
            ScheduleDay.provider_id.is_(None),
        )
    )
    for day_data in data.hours.weekly_schedule:
        parsed_open = _parse_time(day_data.open_time)
        parsed_close = _parse_time(day_data.close_time)
        if day_data.is_active and (parsed_open is None or parsed_close is None):
            raise HTTPException(status_code=400, detail=f"Day {day_data.day_of_week}: open/close time required when active")
        day = ScheduleDay(
            org_id=org.id,
            day_of_week=day_data.day_of_week,
            is_active=day_data.is_active,
            open_time=parsed_open,
            close_time=parsed_close,
            break_start=_parse_time(day_data.break_start),
            break_end=_parse_time(day_data.break_end),
        )
        db.add(day)

    # Update agent persona — keep Organization.custom_prompts for legacy, but
    # frontend Agent page reads OrgConfig.custom_prompts via /dashboard/agent-config
    persona_org = dict(org.custom_prompts) if isinstance(org.custom_prompts, dict) else {}
    persona_org.update({
        "agent_name": data.agent.agent_name,
        "greeting_message": data.agent.greeting_message,
        "tone": data.agent.tone,
    })
    org.custom_prompts = persona_org

    # Normalize onboarding tone to Agent page's allowed values
    raw_tone = (data.agent.tone or "").lower()
    if "casual" in raw_tone or "warm" in raw_tone:
        norm_tone = "casual"
    elif "formal" in raw_tone or "polite" in raw_tone:
        norm_tone = "formal"
    elif "friendly" in raw_tone:
        norm_tone = "friendly"
    elif "empathetic" in raw_tone:
        norm_tone = "empathetic"
    elif "concise" in raw_tone or "direct" in raw_tone:
        norm_tone = "professional"
    else:
        norm_tone = data.agent.tone or "professional"

    persona_cfg = dict(org_config.custom_prompts) if isinstance(org_config.custom_prompts, dict) else {}
    persona_cfg.update({
        "agent_name": data.agent.agent_name,
        "greeting_message": data.agent.greeting_message,
        "tone": norm_tone,
        "industry_context": data.business.industry_type,
    })
    org_config.custom_prompts = persona_cfg

    # Update config fields (these are what /dashboard/agent-config returns)
    org_config.require_otp = data.agent.require_otp
    org_config.max_booking_days_ahead = data.hours.booking_window_days

    await db.commit()

    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.CONFIG_UPDATE,
        resource_type="organization",
        resource_id=str(user.org_id),
        new_values={"action": "onboarding_complete", "industry_type": org.industry_type},
    )

    return OnboardingResponse(success=True, message="Onboarding completed successfully")


@router.get("/onboarding/hours-template", response_model=List[OnboardingHoursDay])
async def get_hours_template(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a default weekly hours template (Mon-Fri 9-5, weekends closed)."""
    return [
        OnboardingHoursDay(day_of_week=i, is_active=i < 5, open_time="09:00", close_time="17:00")
        for i in range(7)
    ]
