"""
Widget Config API — public endpoint for chat widget initialization.
Returns agent branding, greeting, and field definitions for a given org API key.
Supports both API key (legacy) and widget token (recommended).
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from typing import Optional, Literal

from app.db.database import get_db
from app.crud.organization import validate_api_key
from app.models.organization import Organization
from app.models.config import OrgConfig, MeetingMode, MeetingProviderDefault
from app.models.session import ChatSession
from app.models.message import ChatMessage
from app.models.user import User
from datetime import datetime
from app.models.booking import BusinessSchedule
from app.engine.slot_engine import (
    compute_slot_inventory,
    apply_scheduling_policy_to_inventory,
)
from app.engine.scheduling_policy import resolve_effective_timezone_policy
from app.engine.user_visible_text import extract_user_visible_text
from app.core.limiter import limiter
from app.crud.monitoring import create_system_alert
from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_org_from_api_key(
    api_key: str, db: AsyncSession
) -> Optional[Organization]:
    """Look up org by API key (supports grace-period key rotation)."""
    return await validate_api_key(db, api_key)


async def _get_org_from_widget_token(
    token: str, db: AsyncSession
) -> Optional[Organization]:
    """Look up org by its widget token."""
    result = await db.execute(select(OrgConfig).where(OrgConfig.widget_token == token))
    config = result.scalars().first()
    if not config:
        return None
    result = await db.execute(
        select(Organization).where(Organization.id == config.org_id)
    )
    return result.scalars().first()


def _build_config_response(org: Organization, config: Optional[OrgConfig]) -> dict:
    """Build widget config response from org and config objects."""
    custom = (
        config.custom_prompts
        if config and isinstance(config.custom_prompts, dict)
        else {}
    )
    agent_name = custom.get("agent_name") or org.name or "Booking Assistant"
    greeting = custom.get("greeting_message") or (
        f"Hello! I'm {agent_name}. How can I help you schedule an appointment today?"
    )
    brand_color = (config.ui_brand_color if config else None) or "#3B82F6"
    required_fields = (config.required_fields if config else None) or [
        "name",
        "email",
        "phone",
        "date",
        "time",
    ]
    field_definitions = (
        config.field_definitions if config and config.field_definitions else None
    )
    if not field_definitions:
        from app.schemas.agent import FieldDefinition

        field_definitions = [
            FieldDefinition.from_legacy_name(f).dict() for f in required_fields
        ]
    raw_meeting_mode = (
        config.meeting_mode
        if config and getattr(config, "meeting_mode", None)
        else MeetingMode.OFFLINE_ONLY
    )
    try:
        meeting_mode = (
            raw_meeting_mode
            if isinstance(raw_meeting_mode, MeetingMode)
            else MeetingMode(str(raw_meeting_mode))
        )
    except ValueError:
        meeting_mode = MeetingMode.OFFLINE_ONLY
    allow_hybrid_selection = (
        bool(config.allow_hybrid_selection)
        if config and hasattr(config, "allow_hybrid_selection")
        else True
    )
    raw_provider = (
        config.meeting_provider_default
        if config and getattr(config, "meeting_provider_default", None)
        else MeetingProviderDefault.NONE
    )
    try:
        meeting_provider_default = (
            raw_provider
            if isinstance(raw_provider, MeetingProviderDefault)
            else MeetingProviderDefault(str(raw_provider))
        )
    except ValueError:
        meeting_provider_default = MeetingProviderDefault.NONE
    if meeting_mode == MeetingMode.ONLINE_ONLY:
        available_meeting_types = ["ONLINE"]
    elif meeting_mode == MeetingMode.OFFLINE_ONLY:
        available_meeting_types = ["OFFLINE"]
    elif allow_hybrid_selection:
        available_meeting_types = ["ONLINE", "OFFLINE"]
    else:
        available_meeting_types = ["OFFLINE"]

    return {
        "agent_name": agent_name,
        "greeting_message": greeting,
        "brand_color": brand_color,
        "business_name": custom.get("business_name") or None,
        "business_tagline": custom.get("business_tagline") or None,
        "required_fields": required_fields,
        "field_definitions": field_definitions,
        "meeting_mode": meeting_mode.value
        if isinstance(meeting_mode, MeetingMode)
        else str(meeting_mode),
        "allow_hybrid_selection": allow_hybrid_selection,
        "meeting_provider_default": meeting_provider_default.value
        if isinstance(meeting_provider_default, MeetingProviderDefault)
        else str(meeting_provider_default),
        "available_meeting_types": available_meeting_types,
    }


class WidgetConfigResponse(BaseModel):
    agent_name: str
    greeting_message: str
    brand_color: str
    business_name: Optional[str] = None
    business_tagline: Optional[str] = None
    required_fields: list
    field_definitions: Optional[list] = None
    meeting_mode: str
    allow_hybrid_selection: bool
    meeting_provider_default: str
    available_meeting_types: list[str]


@router.get("/config", response_model=WidgetConfigResponse)
@limiter.limit("60/minute")
async def get_widget_config(
    request: Request,
    api_key: Optional[str] = Query(
        default=None, description="Organization API key (legacy)"
    ),
    widget_token: Optional[str] = Query(
        default=None, description="Widget token (recommended)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Public endpoint — no auth required.

    Accepts EITHER api_key OR widget_token (one is required):
    - api_key: the org's full API key (legacy, visible in embed — NOT recommended)
    - widget_token: the limited-purpose widget token (recommended for embed)

    The widget_token is a separate token stored per-org that only allows:
    fetching config, loading session history, and sending/receiving chat messages.
    It CANNOT access admin endpoints, modify bookings, or view other org data.
    """
    if not api_key and not widget_token:
        raise HTTPException(
            status_code=400,
            detail="Either api_key or widget_token query parameter is required",
        )

    if api_key:
        org = await _get_org_from_api_key(api_key, db)
    else:
        org = await _get_org_from_widget_token(widget_token, db)

    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")

    config_result = await db.execute(
        select(OrgConfig).where(OrgConfig.org_id == org.id)
    )
    config = config_result.scalars().first()

    return WidgetConfigResponse(**_build_config_response(org, config))


class WidgetMessageItem(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    sender_type: str = "ai"
    human_agent_name: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/history", response_model=list[WidgetMessageItem])
@limiter.limit("30/minute")
async def get_widget_history(
    request: Request,
    session_id: str = Query(..., description="Chat session ID"),
    api_key: Optional[str] = Query(
        default=None, description="Organization API key (legacy)"
    ),
    widget_token: Optional[str] = Query(
        default=None, description="Widget token (recommended)"
    ),
    since: Optional[str] = Query(
        default=None, description="Only return messages after this timestamp (ISO format)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Public endpoint — returns message history for widget session resumption.

    Phase 10 role (refactored):
    - Initial history load (widget opens with stored session_id)
    - Browser refresh
    - Missed-message recovery (WebSocket reconnect fallback)
    - NOT the primary real-time channel — that role moved to
      GET /ws/widget (WebSocket) which pushes human_joined,
      human_message, and human_left events directly to the widget.

    Validates the session belongs to the org identified by api_key or widget_token.
    Supports incremental fetch via since parameter.
    """
    if not api_key and not widget_token:
        raise HTTPException(
            status_code=400,
            detail="Either api_key or widget_token query parameter is required",
        )

    if api_key:
        org = await _get_org_from_api_key(api_key, db)
    else:
        org = await _get_org_from_widget_token(widget_token, db)

    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")

    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.org_id == org.id,
        )
    )
    session = session_result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    query = (
        select(ChatMessage, User.full_name.label("human_agent_name"))
        .outerjoin(User, ChatMessage.human_agent_id == User.id)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(200)
    )
    
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
            query = query.where(ChatMessage.created_at > since_dt)
        except ValueError:
            pass  # Invalid timestamp, ignore filter

    msg_result = await db.execute(query)
    rows = msg_result.all()

    return [
        WidgetMessageItem(
            id=str(row.ChatMessage.id),
            role=row.ChatMessage.role,
            content=(
                extract_user_visible_text(row.ChatMessage.content)[0]
                if str(row.ChatMessage.role).lower() in {"agent", "assistant", "ai"}
                else row.ChatMessage.content
            ),
            created_at=row.ChatMessage.created_at.isoformat() if row.ChatMessage.created_at else "",
            sender_type=getattr(row.ChatMessage, "sender_type", "ai"),
            human_agent_name=row.human_agent_name,
            metadata=getattr(row.ChatMessage, "metadata_", None),
        )
        for row in rows
    ]


class WidgetAvailabilitySlot(BaseModel):
    slot_id: str
    label_user_tz: str
    start_user_tz: str
    end_user_tz: str
    start_org_tz: str
    end_org_tz: str
    start_utc: str
    end_utc: str
    available: bool
    reason: Optional[str] = None


class WidgetAvailabilityResponse(BaseModel):
    date: str
    org_timezone: str
    user_timezone: str
    timezone_source: Literal["detected", "user_selected", "fallback_org"]
    timezone_fallback_used: bool
    timezone_fallback_reason: Optional[str] = None
    availability_status: Literal[
        "ok",
        "same_day_disabled",
        "closed_day",
        "exception_closed",
        "fully_booked",
        "no_schedule",
        "no_slots_in_window",
    ]
    policy_reason_code: Optional[Literal["same_day_disabled", "past_time_cutoff"]] = None
    slots: list[WidgetAvailabilitySlot]


def _resolve_effective_timezone(
    requested_tz: Optional[str], org_tz: str, requested_source: Optional[str]
) -> tuple[
    str, Literal["detected", "user_selected", "fallback_org"], bool, Optional[str]
]:
    resolution = resolve_effective_timezone_policy(
        requested_tz=requested_tz,
        org_tz=org_tz,
        requested_source=requested_source,
    )
    return (
        str(resolution["effective_timezone"]),
        resolution["timezone_source"],
        bool(resolution["fallback_used"]),
        resolution["fallback_reason"],
    )


def _format_label_user_tz(dt_user: datetime) -> str:
    return dt_user.strftime("%I:%M %p").lstrip("0")


def _build_virtual_slot_id(org_id: str, start_utc: datetime) -> str:
    return f"virtual|{org_id}|{start_utc.isoformat()}"


@router.get("/availability", response_model=WidgetAvailabilityResponse)
@limiter.limit("60/minute")
async def get_widget_availability(
    request: Request,
    date: str = Query(..., description="Target date in YYYY-MM-DD"),
    tz: Optional[str] = Query(
        default=None, description="Visitor timezone IANA name (e.g. Asia/Kolkata)"
    ),
    timezone_source: Optional[str] = Query(
        default=None, description="detected or user_selected"
    ),
    api_key: Optional[str] = Query(
        default=None, description="Organization API key (legacy)"
    ),
    widget_token: Optional[str] = Query(
        default=None, description="Widget token (recommended)"
    ),
    db: AsyncSession = Depends(get_db),
):
    if not api_key and not widget_token:
        raise HTTPException(
            status_code=400,
            detail="Either api_key or widget_token query parameter is required",
        )
    if api_key:
        org = await _get_org_from_api_key(api_key, db)
    else:
        org = await _get_org_from_widget_token(widget_token, db)

    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from exc

    config_result = await db.execute(
        select(OrgConfig).where(OrgConfig.org_id == org.id)
    )
    config = config_result.scalars().first()
    org_tz = (config.timezone if config and config.timezone else "UTC").strip()
    effective_tz, effective_source, fallback_used, fallback_reason = (
        _resolve_effective_timezone(tz, org_tz, timezone_source)
    )

    if fallback_used and fallback_reason in {"missing_tz", "invalid_tz"}:
        alert_code = (
            "TIMEZONE_FALLBACK_MISSING_TZ"
            if fallback_reason == "missing_tz"
            else "TIMEZONE_FALLBACK_INVALID_TZ"
        )
        try:
            await create_system_alert(
                db,
                SystemAlertCreate(
                    org_id=org.id,
                    component=AlertComponent.TIMEZONE,
                    severity=AlertSeverity.WARNING,
                    code=alert_code,
                    message="Widget availability used organization timezone fallback",
                    request_id=request.headers.get("x-request-id"),
                    trace_id=request.headers.get("x-trace-id"),
                    context={
                        "reason": fallback_reason,
                        "timezone_source": effective_source,
                    },
                ),
            )
        except Exception as alert_err:
            logger.warning("widget.availability.fallback.alert.failed: %s", alert_err)

    try:
        org_zone = ZoneInfo(org_tz)
        normalized_org_tz = org_zone.key
    except Exception:
        org_zone = ZoneInfo("UTC")
        normalized_org_tz = "UTC"
    user_zone = ZoneInfo(effective_tz)
    slot_inventory, availability_status = await compute_slot_inventory(
        db, org.id, target_date, org_config=config
    )
    allow_same_day_booking = bool(
        config.allow_same_day_booking if config else False
    )
    slot_cutoff_minutes = config.slot_cutoff_minutes if config else 5
    slot_inventory, availability_status, policy_reason_code = (
        apply_scheduling_policy_to_inventory(
            slot_inventory,
            target_date,
            base_status=availability_status,
            effective_timezone=effective_tz,
            allow_same_day_booking=allow_same_day_booking,
            slot_cutoff_minutes=slot_cutoff_minutes,
            now_utc=datetime.now(timezone.utc),
        )
    )
    schedule_result = await db.execute(
        select(BusinessSchedule.slot_duration_minutes).where(
            BusinessSchedule.org_id == org.id
        )
    )
    slot_duration_minutes = schedule_result.scalar() or 60
    slots: list[WidgetAvailabilitySlot] = []
    for item in slot_inventory:
        start_utc = item["start_utc"]
        end_utc = item["end_utc"]
        start_org = start_utc.astimezone(org_zone)
        end_org = end_utc.astimezone(org_zone)
        start_user = start_utc.astimezone(user_zone)
        end_user = end_utc.astimezone(user_zone)
        slot_id = _build_virtual_slot_id(str(org.id), start_utc)
        slots.append(
            WidgetAvailabilitySlot(
                slot_id=slot_id,
                label_user_tz=_format_label_user_tz(start_user),
                start_user_tz=start_user.isoformat(),
                end_user_tz=end_user.isoformat(),
                start_org_tz=start_org.isoformat(),
                end_org_tz=end_org.isoformat(),
                start_utc=start_utc.isoformat(),
                end_utc=end_utc.isoformat(),
                available=item["available"],
                reason=item["reason"],
            )
        )

    return WidgetAvailabilityResponse(
        date=target_date.isoformat(),
        org_timezone=normalized_org_tz,
        user_timezone=effective_tz,
        timezone_source=effective_source,
        timezone_fallback_used=fallback_used,
        timezone_fallback_reason=fallback_reason,
        availability_status=availability_status,
        policy_reason_code=policy_reason_code,
        slots=slots,
    )
