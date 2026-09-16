"""WhatsApp QR pairing endpoints (Baileys transport via wa-gateway).

Every route resolves the connection row by the authenticated user's org_id —
the frontend never supplies an organization or connection id, so one org can
never touch another org's QR, session, or credentials. Write operations
additionally require a non-impersonated session.
"""

import hmac
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from cachetools import TTLCache
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.v1.endpoints.auth import deny_impersonation, get_current_user
from app.core.audit import log_audit_event
from app.core import wa_gateway
from app.core.wa_gateway import WaGatewayError
from app.core.limiter import limiter
from app.core.whatsapp_debouncer import get_session_lock, is_duplicate
from app.db.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.models.whatsapp_connection import WhatsAppConnection, WhatsAppConnectionStatus
from app.schemas.audit import AuditAction
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Inbound per-sender rate limit for the QR transport (mirrors Meta webhook:
# max 10 messages per sender per 60 seconds).
_inbound_rate_cache: TTLCache = TTLCache(maxsize=10000, ttl=60)


class ConnectionSnapshot(BaseModel):
    status: str
    phone_number: Optional[str] = None
    display_name: Optional[str] = None
    connected_at: Optional[str] = None
    disconnected_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    booking_enabled: bool = False
    booking_active: bool = False
    booking_inactive_reason: Optional[str] = None


class QrResponse(BaseModel):
    qr_data_url: str
    issued_at: int
    expires_at: int


class BookingToggleRequest(BaseModel):
    enabled: bool


def _booking_state(
    row: Optional[WhatsAppConnection],
) -> tuple[bool, bool, Optional[str]]:
    """Effective WhatsApp booking state.

    Booking autoreplies work if and only if the admin toggle is ON.
    Meeting mode (online/offline/hybrid) describes the meeting type, not
    channel permission, so it plays no part here.
    Returns (toggle_enabled, effective_active, inactive_reason).
    """
    enabled = bool(row is not None and getattr(row, "whatsapp_booking_enabled", False))
    if not enabled:
        return enabled, False, "disabled_by_admin"
    return enabled, True, None


def _snapshot(row: Optional[WhatsAppConnection]) -> ConnectionSnapshot:
    if row is None:
        return ConnectionSnapshot(status="NOT_CONNECTED")
    enabled, active, reason = _booking_state(row)
    return ConnectionSnapshot(
        status=row.status,
        phone_number=row.phone_number,
        display_name=row.display_name,
        connected_at=row.connected_at.isoformat() if row.connected_at else None,
        disconnected_at=row.disconnected_at.isoformat() if row.disconnected_at else None,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
        booking_enabled=enabled,
        booking_active=active,
        booking_inactive_reason=reason,
    )


async def _get_row(db: AsyncSession, org_id: object) -> Optional[WhatsAppConnection]:
    res = await db.execute(
        select(WhatsAppConnection).where(WhatsAppConnection.org_id == org_id)  # type: ignore[arg-type]
    )
    return res.scalars().first()


def _apply_gateway_status(row: WhatsAppConnection, gw: dict) -> None:
    """Merge live socket truth into the row (identity + mapped status)."""
    gw_status = str(gw.get("status") or "")
    # Always sync identity when gateway provides it — including clearing
    # stale display_name when new account has no profile name set.
    if "phoneNumber" in gw:
        row.phone_number = str(gw["phoneNumber"])[:32] if gw["phoneNumber"] else None
    if "displayName" in gw:
        row.display_name = str(gw["displayName"])[:255] if gw["displayName"] else None
    mapping = {
        "connected": WhatsAppConnectionStatus.CONNECTED,
        "awaiting_qr": WhatsAppConnectionStatus.PENDING_QR,
        "starting": WhatsAppConnectionStatus.PENDING_QR,
        "reconnecting": WhatsAppConnectionStatus.DISCONNECTED,
        "logged_out": WhatsAppConnectionStatus.LOGGED_OUT,
    }
    mapped = mapping.get(gw_status)
    if mapped:
        row.status = mapped
        now = datetime.now(timezone.utc)
        if mapped == WhatsAppConnectionStatus.CONNECTED:
            row.connected_at = row.connected_at or now
            row.last_seen_at = now
        elif mapped == WhatsAppConnectionStatus.LOGGED_OUT:
            row.disconnected_at = now


def _audit(request: Request, background_tasks: BackgroundTasks, user: User, action: AuditAction) -> None:
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=action,
        resource_type="whatsapp_connection",
        resource_id=str(user.org_id),
        old_values=None,
        new_values=None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/dashboard/whatsapp-qr/connect/start", response_model=ConnectionSnapshot)
@limiter.limit("10/minute")
async def connect_start(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user.org_id)
    if row is not None and row.status == WhatsAppConnectionStatus.CONNECTED:
        raise HTTPException(status_code=409, detail="WhatsApp already connected. Disconnect first to re-pair.")
    if row is None:
        row = WhatsAppConnection(org_id=user.org_id, status=WhatsAppConnectionStatus.PENDING_QR)
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # Concurrent double-click: UNIQUE(org_id) — use the winner's row.
            await db.rollback()
            row = await _get_row(db, user.org_id)
            if row is None:  # pragma: no cover — defensive
                raise HTTPException(status_code=500, detail="Could not start pairing")
    else:
        row.status = WhatsAppConnectionStatus.PENDING_QR
        row.disconnected_at = None
        await db.flush()
    try:
        gw = await wa_gateway.start_session(str(row.id))
    except WaGatewayError as exc:
        await db.rollback()
        logger.warning("wa gateway start failed")
        raise HTTPException(status_code=502, detail=str(exc))
    _apply_gateway_status(row, gw)
    await db.commit()
    await db.refresh(row)
    _audit(request, background_tasks, user, AuditAction.WHATSAPP_QR_CONNECT)
    logger.info("whatsapp-qr pairing started org=%s", user.org_id)
    return _snapshot(row)


@router.get("/dashboard/whatsapp-qr/qr", response_model=QrResponse)
async def get_qr(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No WhatsApp pairing in progress. Start connecting first.")
    try:
        qr = await wa_gateway.get_qr(str(row.id))
    except WaGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not qr:
        raise HTTPException(status_code=404, detail="NO_FRESH_QR")
    return QrResponse(
        qr_data_url=str(qr.get("qrDataUrl") or ""),
        issued_at=int(qr.get("issuedAt") or 0),
        expires_at=int(qr.get("expiresAt") or 0),
    )


@router.get("/dashboard/whatsapp-qr/status", response_model=ConnectionSnapshot)
async def get_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user.org_id)
    if row is None:
        return _snapshot(None)
    try:
        gw = await wa_gateway.get_session_status(str(row.id))
    except WaGatewayError as exc:
        logger.warning("wa gateway status failed org=%s", user.org_id)
        raise HTTPException(status_code=502, detail=str(exc))
    if gw is None:
        # Gateway lost the socket (e.g. restarted before resume): re-drive it,
        # then report. The socket resumes from stored creds — no re-scan.
        try:
            gw = await wa_gateway.start_session(str(row.id))
        except WaGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    _apply_gateway_status(row, gw or {})
    await db.commit()
    await db.refresh(row)
    return _snapshot(row)


@router.post("/dashboard/whatsapp-qr/disconnect", response_model=ConnectionSnapshot)
@limiter.limit("10/minute")
async def disconnect(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user.org_id)
    if row is None or row.status == WhatsAppConnectionStatus.LOGGED_OUT:
        raise HTTPException(status_code=404, detail="No WhatsApp connection to disconnect.")
    try:
        await wa_gateway.logout_session(str(row.id))
    except WaGatewayError as exc:
        logger.warning("wa gateway logout failed org=%s", user.org_id)
        raise HTTPException(status_code=502, detail=str(exc))
    row.status = WhatsAppConnectionStatus.LOGGED_OUT
    row.disconnected_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    _audit(request, background_tasks, user, AuditAction.WHATSAPP_QR_DISCONNECT)
    logger.info("whatsapp-qr disconnected org=%s", user.org_id)
    return _snapshot(row)


@router.post("/dashboard/whatsapp-qr/reconnect", response_model=ConnectionSnapshot)
@limiter.limit("10/minute")
async def reconnect(
    request: Request,
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user.org_id)
    if row is None or row.status == WhatsAppConnectionStatus.LOGGED_OUT:
        raise HTTPException(
            status_code=409,
            detail="Session was unlinked. Start connecting again to scan a fresh QR.",
        )
    try:
        gw = await wa_gateway.reconnect_session(str(row.id))
    except WaGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if gw is None:
        # Gateway forgot the socket: a normal start resumes from stored creds.
        try:
            gw = await wa_gateway.start_session(str(row.id))
        except WaGatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    _apply_gateway_status(row, gw or {})
    await db.commit()
    await db.refresh(row)
    logger.info("whatsapp-qr reconnect requested org=%s", user.org_id)
    return _snapshot(row)


@router.post("/dashboard/whatsapp-qr/booking-toggle", response_model=ConnectionSnapshot)
@limiter.limit("10/minute")
async def booking_toggle(
    body: BookingToggleRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db),
):
    """Admin kill switch for WhatsApp booking autoreplies.

    Connecting the number alone never enables replies — only this toggle does.
    """
    row = await _get_row(db, user.org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No WhatsApp connection. Connect first.")
    row.whatsapp_booking_enabled = bool(body.enabled)
    if body.enabled:
        # WhatsApp bookings verify over email by default — enabling booking
        # auto-enables email OTP (never auto-disables). Phone OTP remains
        # independently controllable so OTP can still be sent on WhatsApp
        # even while WhatsApp booking is on.
        from app.models.config import OrgConfig as _OrgConfig

        _cfg_res = await db.execute(
            select(_OrgConfig).where(_OrgConfig.org_id == user.org_id)  # type: ignore[arg-type]
        )
        _cfg = _cfg_res.scalars().first()
        if _cfg is not None and not bool(getattr(_cfg, "require_otp", True)):
            _cfg.require_otp = True
            logger.info(
                "whatsapp-qr booking toggle auto-enabled email OTP org=%s",
                user.org_id,
            )
    await db.commit()
    await db.refresh(row)
    _audit(request, background_tasks, user, AuditAction.WHATSAPP_QR_BOOKING_TOGGLE)
    logger.info(
        "whatsapp-qr booking toggle org=%s enabled=%s",
        user.org_id,
        row.whatsapp_booking_enabled,
    )
    return _snapshot(row)


# ── Internal inbound from wa-gateway (service-key auth, no JWT) ──────────
class QrInboundPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connectionId: str
    messageId: str
    from_number: str = Field(alias="from")
    chatJid: str
    text: str
    optionId: Optional[str] = Field(default=None, alias="optionId")
    timestamp: Optional[int] = None
    pushName: Optional[str] = None


async def _require_gateway_key(request: Request) -> None:
    want = settings.WA_GATEWAY_SERVICE_KEY or ""
    got = request.headers.get("X-Gateway-Key", "")
    if not want or not hmac.compare_digest(got, want):
        raise HTTPException(status_code=403, detail="Invalid gateway credentials")


@router.post("/internal/whatsapp-qr/inbound")
async def qr_inbound(
    payload: QrInboundPayload,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_gateway_key),
):
    """Receive a normalized 1:1 customer message from the gateway.

    Resolves the org STRICTLY from the connection row (never from the caller),
    then reuses the exact engine path as the Meta webhook: dedup, session,
    lock, process_message, reply payload.
    """
    import uuid as _uuid
    import time as _time

    try:
        connection_uuid = _uuid.UUID(str(payload.connectionId))
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown connection")
    # Boundary log: receipt metadata only (no content, no credentials).
    _sender_tail = "".join(c for c in str(payload.from_number or "") if c.isdigit())[-4:]
    logger.info(
        "qr inbound receipt conn=%s msgid=%s sender_last4=%s text_len=%s",
        str(payload.connectionId)[:8],
        str(payload.messageId)[:24],
        _sender_tail or "none",
        len(str(payload.text or "")),
    )
    row = (
        await db.execute(
            select(WhatsAppConnection).where(WhatsAppConnection.id == connection_uuid)
        )
    ).scalars().first()
    if row is None:
        logger.warning("qr inbound unknown connection conn=%s", str(payload.connectionId)[:8])
        raise HTTPException(status_code=404, detail="Unknown connection")

    org_res = await db.execute(select(Organization).where(Organization.id == row.org_id))
    org = org_res.scalars().first()
    if org is None or not org.is_active:
        logger.info("qr inbound ignored (org missing/inactive)")
        return {"status": "ignored"}

    # Namespace away from Meta-webhook message ids (both transports live
    # side by side until the P10 cutover).
    if is_duplicate(f"qr:{payload.messageId}"):
        return {"status": "duplicate"}

    from_phone = "".join(c for c in str(payload.from_number or "") if c.isdigit())
    text = str(payload.text or "").strip()
    if len(from_phone) < 7 or not text:
        return {"status": "ignored"}

    # OTP codes and knowledge questions must never be rate-limited — they are
    # critical paths (booking confirmation and RAG) and knowledge bursts are
    # expected (author? → summary? → price?).
    is_otp_code = bool(re.match(r"^\d{6}$", text))
    is_knowledge = False
    try:
        from app.engine.agent.graph import _is_potential_knowledge_question

        is_knowledge = _is_potential_knowledge_question(text)
    except Exception:
        is_knowledge = False
    if not is_otp_code and not is_knowledge:
        count = _inbound_rate_cache.get(from_phone, 0)
        if count >= 10:
            logger.warning("QR inbound rate limit exceeded")
            return {"status": "rate_limited"}
        _inbound_rate_cache[from_phone] = count + 1

    from app.crud import chat as crud_chat
    from app.engine import runtime_router
    from app.core import whatsapp_client
    from app.models.config import OrgConfig

    cfg_res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    org_config = cfg_res.scalars().first()
    if org_config is None:  # pragma: no cover — every org has one
        return {"status": "ignored"}

    # Booking kill switch: replies work only when the admin toggle is ON.
    # Merely connecting the number never enables replies.
    _bk_enabled, _bk_active, _bk_reason = _booking_state(row)
    if not _bk_active:
        logger.info(
            "qr inbound ignored (whatsapp booking disabled: %s)",
            _bk_reason or "unknown",
        )
        return {"status": "ignored", "reason": _bk_reason or "whatsapp_booking_disabled"}

    chat_session = await crud_chat.get_or_create_channel_session(
        db, str(org.id), "whatsapp", from_phone
    )
    # Structured directive tap — handle without LLM if text matches active directive's option
    try:
        _active_id = None
        if isinstance(chat_session.collected_data, dict):
            _active_id = chat_session.collected_data.get("active_directive_id")
        if _active_id:
            from app.engine.booking_directive.service import get_by_id as _get_dir
            from app.engine.booking_directive import handler as _dir_handler

            _dir = await _get_dir(db, _active_id)
            if _dir and _dir.status == "PENDING" and _dir.customer_id == from_phone:
                # Check expiry
                from datetime import datetime, timezone as _tz

                if _dir.expires_at and _dir.expires_at < datetime.now(_tz.utc):
                    _dir.status = "EXPIRED"
                    await db.flush()
                    logger.info(f"Directive {str(_dir.id)[:8]} expired on tap")
                else:
                    # Prefer stable optionId from Baileys (buttonId/rowId) if present, else match text
                    _matched = None
                    _incoming_oid = str(getattr(payload, "optionId", "") or "").strip()
                    if _incoming_oid:
                        for _opt in (_dir.options or []):
                            if str(_opt.get("id") or "").strip() == _incoming_oid:
                                _matched = _opt
                                break
                    if not _matched:
                        _norm_text = text.strip().lower()
                        for _opt in (_dir.options or []):
                            _label = str(_opt.get("label") or "").strip().lower()
                            _value = str(_opt.get("value") or "").strip().lower()
                            _oid = str(_opt.get("id") or "").strip().lower()
                            if _norm_text in (_label, _value, _oid) or _norm_text == _label.replace(" ", ""):
                                _matched = _opt
                                break
                            try:
                                _idx = int(_norm_text) - 1
                                if 0 <= _idx < len(_dir.options or []) and _dir.options[_idx] == _opt:
                                    _matched = _opt
                                    break
                            except Exception:
                                pass
                    if _matched:
                        _res = await _dir_handler.handle_directive_response(
                            db, directive_id=_dir.id, customer_id=from_phone, option_id=_matched.get("id"), booking_id=None
                        )
                        if _res.get("ok"):
                            # Update collected_data with selected value
                            try:
                                _field = (_dir.meta or {}).get("field_name") or (_dir.type.lower().replace("select_", "") if _dir.type.startswith("SELECT_") else None)
                                if _field:
                                    _cd = dict(chat_session.collected_data or {})
                                    # Map directive type to field name
                                    _field_map = {"SELECT_DATE": "date", "SELECT_SLOT": "time", "SELECT_TIME": "time"}
                                    _fname = _field_map.get(_dir.type, _field)
                                    _cd[_fname] = _matched.get("value") or _matched.get("label")
                                    # Clear active directive, recalc missing
                                    _cd.pop("active_directive_id", None)
                                    chat_session.collected_data = _cd
                                    from app.engine.evaluator import StateEvaluator
                                    from app.schemas.agent import parse_field_definitions, order_field_definitions

                                    _fds = order_field_definitions(parse_field_definitions(chat_session.collected_data.get("field_definitions_snapshot") or org_config.field_definitions or org_config.required_fields))
                                    _req = [f.name for f in _fds if f.required]
                                    chat_session.missing_fields = StateEvaluator.calculate_missing_fields(_req, _cd)
                                    await db.commit()
                                    logger.info(f"Directive {str(_dir.id)[:8]} completed via tap: {str(_matched.get('label'))[:20]}")
                            except Exception as _e:
                                logger.warning(f"Directive tap state update failed: {_e}")
                            # Generate next directive or proceed to booking via normal engine on next turn
                            # For now, let next turn's engine handle next step — return a short ack
                            # But to avoid an extra round-trip, we can directly invoke the engine for the next step
                            # Fall through to normal engine with the selected value as message_text
                            text = str(_matched.get("value") or _matched.get("label") or text)
    except Exception as _e:
        logger.warning(f"Directive tap handling failed, falling back to LLM: {_e}")

    logger.info("qr inbound session=%s (org resolved, engine next)", chat_session.session_id)
    session_lock = get_session_lock(str(chat_session.session_id))
    async with session_lock:
        logger.info("qr inbound invoking route_whatsapp_message()")
        _t0 = _time.monotonic()
        try:
            action = await runtime_router.route_whatsapp_message(
                db=db,
                org=org,
                org_config=org_config,
                chat_session=chat_session,
                message_text=text,
                background_tasks=background_tasks,
            )
        except Exception:
            logger.exception("qr inbound engine raised")
            raise
        _dt = _time.monotonic() - _t0
        # If a BookingDirective was just created for this turn, render it via the
        # dedicated WhatsAppDirectiveRenderer (stable IDs) instead of the generic
        # field_meta path. This ensures the outgoing Baileys payload contains
        # the persisted directive's option IDs.
        _active_id_after = None
        try:
            if isinstance(chat_session.collected_data, dict):
                _active_id_after = chat_session.collected_data.get("active_directive_id")
        except Exception:
            pass
        # ── Simple text booking system — directive interactive disabled per product decision ──
        # No interactive buttons/lists, just plain text. Booking engine + directive remain source of truth for validation,
        # but WhatsApp presentation is text-only (user types date like "17 sep" or "18/09/2026").
        wa_msg = None
        if _active_id_after:
            try:
                from app.engine.booking_directive.service import get_by_id as _get_dir2

                _dir_after = await _get_dir2(db, _active_id_after)
                if _dir_after and _dir_after.status == "PENDING":
                    _title = str(getattr(_dir_after, "title", "") or "").strip()
                    _desc = str(getattr(_dir_after, "description", "") or "").strip()
                    _opts = list(getattr(_dir_after, "options", None) or [])
                    # If directive description empty (old rows), generate horizon/hours on the fly for smooth text
                    if not _desc:
                        try:
                            if _dir_after.type == "SELECT_DATE":
                                from app.engine.message_processor import _format_available_dates_for_prompt
                                _tz_q = str(getattr(org_config, "timezone", None) or "UTC")
                                _max_q = int(getattr(org_config, "max_booking_days_ahead", 30) or 30)
                                _desc = _format_available_dates_for_prompt(_tz_q, _max_q)
                            elif _dir_after.type in ("SELECT_SLOT", "SELECT_TIME"):
                                from app.engine.message_processor import _format_time_prompt_for_whatsapp
                                from app.models.booking import BusinessSchedule as _BSq, ScheduleDay as _SDq, ScheduleException as _SEq
                                from sqlalchemy.future import select as _Selq
                                # try to get selected date from collected_data
                                _sel_date = (chat_session.collected_data or {}).get("date") or ""
                                _weekly_q = None
                                _excs_q = None
                                try:
                                    _sdq_res = await db.execute(_Selq(_SDq).where(_SDq.org_id == org.id))
                                    _sdq = _sdq_res.scalars().all()
                                    _weekly_q = [{"day_of_week": r.day_of_week, "is_active": r.is_active, "open_time": r.open_time, "close_time": r.close_time} for r in _sdq]
                                    _seq_res = await db.execute(_Selq(_SEq).where(_SEq.org_id == org.id))
                                    _seq = _seq_res.scalars().all()
                                    _excs_q = [{"exception_date": r.exception_date, "exception_type": str(r.exception_type), "custom_open": r.custom_open, "custom_close": r.custom_close} for r in _seq]
                                except Exception:
                                    pass
                                _slot_mins_q = None
                                try:
                                    _bsq_res = await db.execute(_Selq(_BSq).where(_BSq.org_id == org.id))
                                    _bsq = _bsq_res.scalars().first()
                                    if _bsq:
                                        _slot_mins_q = getattr(_bsq, "slot_duration_minutes", None)
                                except Exception:
                                    pass
                                _tz_q2 = str(getattr(org_config, "timezone", None) or "UTC")
                                _desc = _format_time_prompt_for_whatsapp(str(_sel_date or ""), _tz_q2, _slot_mins_q, weekly_schedule=_weekly_q, exceptions=_excs_q)
                        except Exception:
                            pass
                    _parts = [p for p in [_title, _desc] if p]
                    if _opts:
                        _lines = [f"{i}. {str(o.get('label') or o.get('id') or '').strip()}" for i, o in enumerate(_opts[:7], 1)]
                        _parts.append("\n".join(_lines))
                    _body = "\n\n".join(_parts).strip()
                    wa_msg = {"type": "text", "body": _body[:4096]}
                    logger.info(f"Rendered simple-text directive type={_dir_after.type} directive={str(_dir_after.id)[:8]} opts={len(_opts)}")
            except Exception as _e:
                logger.warning(f"Directive render failed, falling back: {_e}")
        if wa_msg is None:
            # Non-directive path — keep deterministic booking engine rendering for now.
            # Knowledge-tone presentation for ASK_QUESTION will be handled via graph's RAG path (already tone-aware);
            # presenter for text is reserved for Phase 5 when we wire retrieved chunks explicitly.
            wa_msg = whatsapp_client.format_action_for_whatsapp(action)
        # Approved Marketing templates via Cloud API (language `en`), Baileys stays as fallback.
        # Sends Cloud copy for Meta compliance; the Baileys `wa_msg` above is still
        # returned so the QR thread keeps working (dual-sender, same as OTP today).
        try:
            from app.core import whatsapp_templates as _wtemplates

            if _wtemplates.templates_enabled():
                _directive = getattr(action, "widget_directive", "") or ""
                _fmeta = getattr(action, "field_meta", None) or {}
                _sdata = chat_session.state_data if isinstance(getattr(chat_session, "state_data", None), dict) else {}
                _cname = str(_sdata.get("name") or "").strip() or None
                try:
                    _cname = _cname or str((_sdata.get("pushName") or "")).strip() or "there"
                except Exception:
                    _cname = _cname or "there"
                _business = str(getattr(org, "name", "") or "Dotstark Technologies")[:60]
                # Cloud `to` must be a routable E.164 number. `from_phone` on
                # Baileys is often a LID (e.g. 1443.../2482...), not the real
                # MSISDN — Meta accepts it (wamid) but never delivers. Prefer
                # the validated phone already in state_data (now +91 by
                # default via validators), fall back to from_phone.
                import re as _re_tpl

                _e164 = "".join(_re_tpl.findall(r"\d", str(_sdata.get("phone") or "")))
                if len(_e164) == 10 and _e164[0] in "6789":
                    _e164 = "91" + _e164
                _cloud_to = _e164 if len(_e164) >= 12 else from_phone
                if _directive == "REQUIRE_EMAIL_INPUT":
                    _tres = await _wtemplates.send_request_email(_cloud_to, _cname if _cname != "there" else "there")
                    logger.info("booking-template email to=%s*** sent=%s wamid=%s", str(_cloud_to)[:6], _tres.get("success"), _tres.get("wamid"))
                elif _directive == "REQUIRE_PHONE_INPUT":
                    _tres = await _wtemplates.send_request_phone(_cloud_to)
                    logger.info("booking-template phone to=%s*** sent=%s wamid=%s", str(_cloud_to)[:6], _tres.get("success"), _tres.get("wamid"))
                elif _directive == "TEXT_INPUT" and str(_fmeta.get("name") or "") == "name":
                    _tres = await _wtemplates.send_welcome(_cloud_to, _cname, _business)
                    logger.info("booking-template welcome to=%s*** sent=%s wamid=%s", str(_cloud_to)[:6], _tres.get("success"), _tres.get("wamid"))
        except Exception as _e:
            logger.warning(f"booking-template cloud send failed, Baileys fallback kept: {_e}")
        logger.info(
            "qr inbound engine done in %.1fs reply_type=%s reply_len=%s",
            _dt,
            (wa_msg or {}).get("type"),
            len(str((wa_msg or {}).get("body") or "")),
        )
    logger.info("qr inbound processed org=%s session=%s", org.id, chat_session.session_id)
    return {"status": "ok", "session_id": str(chat_session.session_id), "reply": wa_msg}
