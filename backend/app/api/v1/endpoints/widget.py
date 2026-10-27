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
from app.models.booking import BusinessSchedule, ScheduleDay
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
    # Ask-AI section greeting: Q&A-flavored, never booking-flavored (that
    # section only answers questions — booking/cancel/reschedule redirect
    # to the Book Appointment button). Admins can override it with the
    # custom_prompts "ask_greeting_message" key.
    ask_greeting = custom.get("ask_greeting_message") or (
        f"Hello! I'm {agent_name}. Ask me anything and I'll do my best to help!"
    )
    brand_color = (config.ui_brand_color if config else None) or "#3B82F6"
    logo_url = (getattr(config, "logo_url", None) if config else None) or None
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

    # Widget theme (Feature 2): stored in custom_prompts.ui_theme so no DB
    # migration is needed. Defaults keep existing embeds unchanged.
    _custom_dict = custom if isinstance(custom, dict) else {}
    _raw_theme = _custom_dict.get("ui_theme")
    raw_theme = _raw_theme if isinstance(_raw_theme, dict) else {}
    ui_theme = {
        "position": str(raw_theme.get("position") or "bottom-right"),
        "border_radius": int(raw_theme.get("border_radius") or 16),
        "font_family": str(raw_theme.get("font_family") or "Inter, system-ui, sans-serif"),
        "dark_mode": bool(raw_theme.get("dark_mode", True)),
        # Branding is always shown (product decision 2025-10): the widget
        # never hides the powered-by strip, any stored flag is ignored.
        "show_branding": True,
        "launcher_shape": str(raw_theme.get("launcher_shape") or "round"),
    }
    if ui_theme["position"] not in ("bottom-right", "bottom-left", "center"):
        ui_theme["position"] = "bottom-right"
    ui_theme["border_radius"] = max(0, min(28, ui_theme["border_radius"]))

    # Quick prompts (suggested questions): stored in
    # custom_prompts.quick_prompts, no DB migration. Sanitized so a bad
    # admin entry can never break the widget. Max 8, title required, each
    # prompt needs message text and/or a link.
    _raw_qp = _custom_dict.get("quick_prompts")
    quick_prompts: list = []
    if isinstance(_raw_qp, list):
        for _item in _raw_qp[:8]:
            if not isinstance(_item, dict):
                continue
            _title = str(_item.get("title") or "").strip()[:60]
            if not _title:
                continue
            _content = str(_item.get("content") or "").strip()[:300]
            _link = str(_item.get("link") or "").strip()[:500]
            if _link and not (
                _link.startswith("http://") or _link.startswith("https://")
            ):
                continue
            if not _content and not _link:
                continue
            _show_at = str(_item.get("show_at") or "both").strip().lower()
            if _show_at not in ("start", "followup", "both"):
                _show_at = "both"
            quick_prompts.append(
                {
                    "title": _title,
                    "content": _content or None,
                    "link": _link or None,
                    "show_at": _show_at,
                }
            )

    return {
        "agent_name": agent_name,
        "greeting_message": greeting,
        "ask_greeting_message": ask_greeting,
        "brand_color": brand_color,
        "logo_url": logo_url,
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
        "require_otp": config.require_otp if config else True,
        "require_phone_otp": config.require_phone_otp if config else False,
        "ui_theme": ui_theme,
        "quick_prompts": quick_prompts,
    }


class WidgetConfigResponse(BaseModel):
    agent_name: str
    greeting_message: str
    ask_greeting_message: str = ""
    brand_color: str
    logo_url: Optional[str] = None
    business_name: Optional[str] = None
    business_tagline: Optional[str] = None
    required_fields: list
    field_definitions: Optional[list] = None
    meeting_mode: str
    allow_hybrid_selection: bool
    meeting_provider_default: str
    available_meeting_types: list[str]
    require_otp: bool = True
    require_phone_otp: bool = False
    # Phase horizon: single source (BusinessSchedule first). The picker
    # clamps to it; backend validation enforces it again at selection.
    max_booking_days_ahead: int = 60
    # Weekdays with no hours (0=Mon..6=Sun) for picker hints.
    closed_weekdays: list = []
    # Phase 3 catalog: active units with providers for pre-chat selection.
    # Absent/empty = org without catalog; widget behavior unchanged.
    has_catalog: bool = False
    catalog: Optional[dict] = None
    # Feature 2: widget theme (position, radius, font, dark mode, branding).
    ui_theme: Optional[dict] = None
    # Quick prompts (suggested questions): [{title, content?, link?, show_at}].
    quick_prompts: Optional[list] = None


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

    response = _build_config_response(org, config)

    # Phase horizon + closed days for the date picker (best-effort; the
    # picker falls back to min=today when absent, backend still enforces).
    try:
        from app.engine.scheduling_policy import (
            resolve_booking_horizon_days as _resolve_horizon,
        )

        _horizon_days, _, _ = await _resolve_horizon(db, org.id, config)
        response["max_booking_days_ahead"] = _horizon_days
        _day_rows = (
            await db.execute(
                select(ScheduleDay).where(
                    ScheduleDay.org_id == org.id,
                    ScheduleDay.provider_id.is_(None),
                )
            )
        ).scalars().all()
        response["closed_weekdays"] = sorted(
            {
                int(r.day_of_week)
                for r in _day_rows
                if not r.is_active and r.day_of_week is not None
            }
        )
    except Exception as horizon_err:
        logger.warning("widget.config.horizon.failed: %s", horizon_err)

    # Phase 3 catalog snapshot (best-effort; never breaks config for orgs
    # without catalog or on lookup errors).
    try:
        from app.services.catalog_service import get_catalog_snapshot

        snapshot = await get_catalog_snapshot(db, org.id)
        response["catalog"] = snapshot
        response["has_catalog"] = bool(snapshot.get("has_catalog"))
    except Exception as catalog_err:
        logger.warning("widget.config.catalog.failed: %s", catalog_err)
        response["catalog"] = {"units": [], "has_catalog": False}
        response["has_catalog"] = False

    return WidgetConfigResponse(**response)


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


@router.get("/session-resume")
@limiter.limit("30/minute")
async def get_widget_session_resume(
    request: Request,
    session_id: str = Query(..., description="Chat session ID"),
    api_key: Optional[str] = Query(
        default=None, description="Organization API key (legacy)"
    ),
    widget_token: Optional[str] = Query(
        default=None, description="Widget token (recommended)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Pending step (directive + field_meta) for a re-joined booking session.

    The widget keeps a separate backend session per section (Ask AI / Start
    Booking) and restores the transcript from /history when the visitor
    switches back or reloads the page. The booking section is fully
    card-driven, so the pending step must re-render as its directive card —
    never as free text. This computes the pending directive from the live
    session state so the widget can render exactly the card the flow would
    have shown next.
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

    from app.crud.chat import get_chat_session as _get_session_state
    from app.schemas.agent import parse_field_definitions, order_field_definitions
    from app.engine.evaluator import StateEvaluator

    chat_session = await _get_session_state(db, session_id)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Ask-AI sessions are free-text Q&A: nothing card-shaped to restore.
    collected = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
    if str(collected.get("_mode") or "").strip().lower() == "ask":
        return {"directive": "TEXT_INPUT", "field_meta": None, "message": None}

    # Finished bookings show the confirmed card, not a stale step.
    if str(getattr(chat_session, "session_status", "") or "").upper() == "CONFIRMED":
        return {
            "directive": "BOOKING_CONFIRMED_CARD",
            "field_meta": {},
            "message": "Your booking has been confirmed.",
        }

    # Field definitions: the session's own snapshot is authoritative (admin
    # may have changed the org config after this session started).
    snapshot = collected.get("field_definitions_snapshot") or []
    if isinstance(snapshot, list) and snapshot:
        field_definitions = order_field_definitions(parse_field_definitions(snapshot))
    else:
        cfg_result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
        org_config = cfg_result.scalars().first()
        if not org_config:
            raise HTTPException(status_code=404, detail="Organization configuration not found")
        field_definitions = order_field_definitions(
            parse_field_definitions(org_config.field_definitions or org_config.required_fields)
        )

    next_action = StateEvaluator.determine_next_action(
        list(chat_session.missing_fields or []),
        str(chat_session.current_intent or "START_BOOKING"),
        field_definitions=field_definitions,
    )
    directive = str(next_action.get("directive") or "TEXT_INPUT")
    field_meta = next_action.get("field_meta")
    message = None

    # All details collected → the pending step is the review card.
    # (PROCESS_BOOKING is a one-turn action, not a restorable UI state.)
    if directive in ("REQUEST_CONFIRMATION", "PROCESS_BOOKING", "BOOKING_SUMMARY_CARD"):
        directive = "REQUEST_CONFIRMATION"
        try:
            from app.engine.message_processor import _build_confirmation_summary

            fd_map = {fd.name: fd for fd in field_definitions}
            message, _ = _build_confirmation_summary(chat_session, fd_map)
        except Exception:
            logger.warning("session-resume summary build failed", exc_info=True)
            message = None

    return {"directive": directive, "field_meta": field_meta, "message": message}


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
    # Suggestions-first (Feature 1): total free slots before truncation so the
    # widget can render "2-3 pills + Show N more" without a second fetch.
    total_available: int = 0
    truncated: bool = False
    suggestion_limit: int = 3


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


def _build_virtual_slot_id(
    org_id: str, start_utc: datetime, provider_id: Optional[str] = None
) -> str:
    if provider_id:
        return f"virtual|{org_id}|{provider_id}|{start_utc.isoformat()}"
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
    unit_id: Optional[str] = Query(
        default=None, description="Department/unit filter (org-mode)"
    ),
    provider_id: Optional[str] = Query(
        default=None, description="Doctor/provider filter (org-mode)"
    ),
    limit: Optional[int] = Query(
        default=None,
        ge=1,
        le=50,
        description="Suggestions-first: return only the first N AVAILABLE slots (e.g. 3). Unavailable slots are omitted when limit is set.",
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
    # Org-mode: validate dept/doctor belong to this org; None = individual flow.
    _eff_provider_id = None
    try:
        from app.services.catalog_service import validate_catalog_selection

        if unit_id or provider_id:
            await validate_catalog_selection(db, org.id, unit_id, provider_id)
        _eff_provider_id = provider_id
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        _eff_provider_id = provider_id
    slot_inventory, availability_status = await compute_slot_inventory(
        db, org.id, target_date, org_config=config, provider_id=_eff_provider_id
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
    # Per-doctor duration wins for org-mode display
    try:
        if _eff_provider_id:
            from app.models.service_unit import Provider as _P

            _prow = (
                await db.execute(
                    select(_P).where(
                        _P.id == _eff_provider_id, _P.org_id == org.id
                    )
                )
            ).scalars().first()
            if _prow is not None and getattr(_prow, "slot_duration_minutes", None):
                slot_duration_minutes = int(_prow.slot_duration_minutes)
    except Exception:
        pass
    slots: list[WidgetAvailabilitySlot] = []
    for item in slot_inventory:
        start_utc = item["start_utc"]
        end_utc = item["end_utc"]
        start_org = start_utc.astimezone(org_zone)
        end_org = end_utc.astimezone(org_zone)
        start_user = start_utc.astimezone(user_zone)
        end_user = end_utc.astimezone(user_zone)
        slot_id = _build_virtual_slot_id(
            str(org.id), start_utc, _eff_provider_id
        )
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

    total_available = sum(1 for s in slots if s.available)
    truncated = False
    suggestion_limit = 3
    if limit is not None and limit > 0:
        suggestion_limit = int(limit)
        available_only = [s for s in slots if s.available]
        if len(available_only) > suggestion_limit:
            slots = available_only[:suggestion_limit]
            truncated = True
        else:
            slots = available_only

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
        total_available=total_available,
        truncated=truncated,
        suggestion_limit=suggestion_limit,
    )


class WidgetFeeResponse(BaseModel):
    has_fee: bool = False
    amount_paise: Optional[int] = None
    currency: Optional[str] = None
    display: Optional[str] = None
    unit_id: Optional[str] = None
    unit_name: Optional[str] = None
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None


@router.get("/fees", response_model=WidgetFeeResponse)
@limiter.limit("60/minute")
async def get_widget_fee(
    request: Request,
    unit_id: Optional[str] = Query(default=None),
    provider_id: Optional[str] = Query(default=None),
    api_key: Optional[str] = Query(
        default=None, description="Organization API key (legacy)"
    ),
    widget_token: Optional[str] = Query(
        default=None, description="Widget token (recommended)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Public effective-fee preview for a unit/provider selection.

    Returns the fee the booking would carry (unit+provider > provider-only >
    unit-only). No fee configured → has_fee=False. Never fails the widget.
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

    from app.services.catalog_service import (
        resolve_effective_fee,
        validate_catalog_selection,
    )

    try:
        unit, provider = await validate_catalog_selection(
            db, org.id, unit_id, provider_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    amount, currency = await resolve_effective_fee(
        db, org.id, unit_id, provider_id
    )
    if not amount:
        return WidgetFeeResponse(has_fee=False)
    return WidgetFeeResponse(
        has_fee=True,
        amount_paise=amount,
        currency=currency,
        display=f"{currency or 'INR'} {(amount / 100):,.2f}",
        unit_id=str(unit.id) if unit else None,
        unit_name=unit.name if unit else None,
        provider_id=str(provider.id) if provider else None,
        provider_name=provider.full_name if provider else None,
    )


class ClientLogRequest(BaseModel):
    event: str
    session_id: Optional[str] = None
    channel: Optional[str] = None
    hasConfirmationResult: Optional[bool] = None
    detail: Optional[str] = None
    phone_masked: Optional[str] = None


@router.get("/invite/{booking_id}")
@limiter.limit("60/minute")
async def widget_booking_invite(
    request: Request,
    booking_id: str,
    widget_token: Optional[str] = Query(default=None),
    api_key: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Feature 3: public .ics download for a confirmed booking.

    Auth: same org scoping as other widget endpoints (widget_token or
    api_key). Only the owning org's booking is served — cross-tenant 404.
    No PII beyond what the customer already gave (name/email in SUMMARY).
    """
    from fastapi.responses import PlainTextResponse

    if not api_key and not widget_token:
        raise HTTPException(status_code=400, detail="Either api_key or widget_token query parameter is required")
    org = await _get_org_from_api_key(api_key, db) if api_key else await _get_org_from_widget_token(widget_token, db)  # type: ignore[arg-type]
    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")
    try:
        import uuid as _uuid

        _bid = _uuid.UUID(str(booking_id))
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid booking id") from exc
    from app.models.booking import Booking as _Booking

    res = await db.execute(select(_Booking).where(_Booking.id == _bid, _Booking.org_id == org.id))
    booking = res.scalars().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    def _fmt_ics(dt) -> str:
        try:
            import datetime as _dt

            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            return ""

    start = getattr(booking, "user_slot_start_at", None) or getattr(booking, "org_slot_start_at", None)
    end = getattr(booking, "user_slot_end_at", None) or getattr(booking, "org_slot_end_at", None)
    summary = f"Appointment with {org.name or 'our team'}"
    description = f"Booked as {(getattr(booking, 'customer_name', '') or '').replace(chr(10), ' ')}"
    if getattr(booking, "meeting_link_url", None):
        description += f"\\nMeeting link: {booking.meeting_link_url}"
    uid = f"{booking.id}@dotstark.booking"
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//DotStark//Booking//EN\r\n"
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:{_fmt_ics(start)}\r\nDTSTART:{_fmt_ics(start)}\r\nDTEND:{_fmt_ics(end)}\r\n"
        f"SUMMARY:{summary}\r\nDESCRIPTION:{description}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    return PlainTextResponse(
        ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=booking-{booking.id}.ics"},
    )


@router.post("/client-log")
@limiter.limit("60/minute")
async def widget_client_log(
    request: Request,
    payload: ClientLogRequest,
    db: AsyncSession = Depends(get_db),
):
    """Lightweight client-side telemetry - widget posts verify/send events here so they appear in docker backend logs."""
    # Mask any phone/detail to avoid PII in logs
    masked_phone = payload.phone_masked or ""
    if masked_phone and len(masked_phone) > 6:
        masked_phone = masked_phone[:3] + "***" + masked_phone[-3:]
    logger.info(
        "widget.client_log event=%s session=%s channel=%s hasConfirmationResult=%s phone=%s detail=%s ip=%s",
        payload.event,
        (payload.session_id or "")[:8] + "***" if payload.session_id else "",
        payload.channel or "",
        str(payload.hasConfirmationResult) if payload.hasConfirmationResult is not None else "",
        masked_phone,
        (payload.detail or "")[:200],
        request.client.host if request.client else "",
    )
    return {"ok": True}
