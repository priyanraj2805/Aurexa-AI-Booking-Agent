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
from app.core.whatsapp_debouncer import (
    get_session_lock,
    is_duplicate,
    release_message,
    check_sender_rate_limit,
    session_lock_key,
)
from app.db.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.models.whatsapp_connection import WhatsAppConnection, WhatsAppConnectionStatus
from app.schemas.audit import AuditAction
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

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


async def _alert_whatsapp_unlinked(db: AsyncSession, row: WhatsAppConnection) -> None:
    """Fire a deduped critical alert + org notification when the number unlinks.

    Best-effort and never raises — status polling must keep working even if
    alerting fails. `create_system_alert` dedupes on (org, component, code),
    so repeated polls bump a counter instead of spamming.
    """
    try:
        from app.crud import monitoring as _mon
        from app.schemas.monitoring import (
            AlertComponent as _AC,
            AlertSeverity as _AS,
            SystemAlertCreate as _SAC,
        )
        from app.core.org_notify import notify_org as _notify

        await _mon.create_system_alert(
            db,
            _SAC(
                org_id=row.org_id,
                component=_AC.WHATSAPP,
                severity=_AS.CRITICAL,
                code="WHATSAPP_LOGGED_OUT",
                message=(
                    "WhatsApp was unlinked from the phone — the booking agent "
                    "cannot reply on WhatsApp until you scan a fresh QR code."
                ),
                context={"connection_id": str(row.id)},
            ),
        )
        await _notify(
            org_id=row.org_id,
            kind="whatsapp_disconnected",
            title="WhatsApp disconnected",
            body=(
                "Your WhatsApp number was unlinked. Open Integrations → "
                "WhatsApp and scan the QR code to reconnect."
            ),
            resource_type="whatsapp_connection",
            resource_id=str(row.id),
        )
    except Exception:
        logger.warning("whatsapp unlink alert failed", exc_info=True)


async def _resolve_whatsapp_alerts(db: AsyncSession, row: WhatsAppConnection) -> None:
    """Clear open unlink alerts once the number is back (re-paired/connected)."""
    try:
        from app.models.system_alert import SystemAlert

        now = datetime.now(timezone.utc)
        res = await db.execute(
            select(SystemAlert).where(
                SystemAlert.org_id == row.org_id,
                SystemAlert.code == "WHATSAPP_LOGGED_OUT",
                SystemAlert.resolved_at.is_(None),
            )
        )
        changed = False
        for alert in res.scalars().all():
            alert.resolved_at = now
            changed = True
        if changed:
            await db.commit()
    except Exception:
        logger.warning("whatsapp alert resolve failed", exc_info=True)


def _audit(request: Request, background_tasks: BackgroundTasks, user: User, action: AuditAction) -> None:    log_audit_event(
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
    # Re-pairing supersedes any open unlink alert.
    await _resolve_whatsapp_alerts(db, row)
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
    _prev_status = row.status
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
    # Transition alerts: unlinking needs a loud, deduped signal (admins
    # otherwise discover it only when customers go silent); recovery clears it.
    if _prev_status != row.status:
        if row.status == WhatsAppConnectionStatus.LOGGED_OUT:
            await _alert_whatsapp_unlinked(db, row)
        elif row.status == WhatsAppConnectionStatus.CONNECTED:
            await _resolve_whatsapp_alerts(db, row)
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
    # A deliberate disconnect supersedes any open unlink alert.
    await _resolve_whatsapp_alerts(db, row)
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
    # Never force-restart a healthy socket: killing a live connection makes
    # WhatsApp replace it (conflict storm) and drops in-flight replies. When
    # the gateway reports connected, sync the row and report — no-op.
    try:
        _live = await wa_gateway.get_session_status(str(row.id))
    except WaGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if _live is not None and str(_live.get("status") or "") == "connected":
        _apply_gateway_status(row, _live)
        await db.commit()
        await db.refresh(row)
        logger.info("whatsapp-qr reconnect no-op (already connected) org=%s", user.org_id)
        return _snapshot(row)
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
    # NOTE: this toggle never touches email/phone OTP flags. An explicit
    # admin OFF must always win — auto-enabling email OTP here silently
    # reverted the Agent-page setting on every toggle (reported bug).
    # Bookings with both OTPs off confirm unverified; the Agent page
    # already warns about that next to the toggles.
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


class QrEventPayload(BaseModel):
    """Gateway lifecycle and delivery push (service-key auth, no JWT)."""

    connectionId: str
    event: str
    sessionId: Optional[str] = None
    fromNumber: Optional[str] = None
    chatJid: Optional[str] = None
    providerMessageId: Optional[str] = None
    error: Optional[str] = None


async def _require_gateway_key(request: Request) -> None:
    want = settings.WA_GATEWAY_SERVICE_KEY or ""
    got = request.headers.get("X-Gateway-Key", "")
    if not want or not hmac.compare_digest(got, want):
        raise HTTPException(status_code=403, detail="Invalid gateway credentials")


@router.post("/internal/whatsapp-qr/events")
async def qr_events(
    payload: QrEventPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_gateway_key),
):
    """Gateway lifecycle push: spontaneous logout / (re)connect.

    Polling-only status goes stale between admin visits, so the gateway
    reports terminal events directly: the DB row, the unlink alert, and
    its resolution all update within seconds, with no admin action.
    Unknown connections and unknown events are ignored (never 500 — the
    gateway must not retry-loop these).
    """
    import uuid as _uuid

    try:
        connection_uuid = _uuid.UUID(str(payload.connectionId))
    except ValueError:
        return {"status": "ignored", "reason": "unknown_connection"}
    row = (
        await db.execute(
            select(WhatsAppConnection).where(WhatsAppConnection.id == connection_uuid)
        )
    ).scalars().first()
    if row is None:
        return {"status": "ignored", "reason": "unknown_connection"}
    event = str(payload.event or "").strip().lower()
    now = datetime.now(timezone.utc)
    if event in {"message_sent", "message_failed", "message_delivered", "message_read"}:
        from app.crud import chat as _crud_chat
        from app.models.session import ChatSession as _ChatSession

        session_id = str(payload.sessionId or "").strip()
        session_uuid = None
        if session_id:
            try:
                session_uuid = _uuid.UUID(session_id)
            except ValueError:
                session_uuid = None
        session = None
        if session_uuid is not None:
            session = (
                await db.execute(
                    select(_ChatSession).where(
                        _ChatSession.id == session_uuid,
                        _ChatSession.org_id == row.org_id,
                        _ChatSession.channel == "whatsapp",
                    )
                )
            ).scalars().first()
        if session is None and payload.fromNumber:
            digits = re.sub(r"\D", "", str(payload.fromNumber))
            if digits:
                session = (
                    await db.execute(
                        select(_ChatSession)
                        .where(
                            _ChatSession.org_id == row.org_id,
                            _ChatSession.channel == "whatsapp",
                            _ChatSession.channel_user_id == digits,
                        )
                        .order_by(_ChatSession.updated_at.desc())
                        .limit(1)
                    )
                ).scalars().first()
        if session is None:
            return {"status": "ignored", "reason": "unknown_session"}
        try:
            if event == "message_sent":
                await _crud_chat.mark_latest_outbound_sent(
                    db,
                    str(session.id),
                    payload.providerMessageId,
                )
            elif event == "message_failed":
                await _crud_chat.mark_latest_outbound_failed(
                    db,
                    str(session.id),
                    payload.error or "WhatsApp gateway send failed",
                )
            else:
                await _crud_chat.apply_delivery_status(
                    db,
                    payload.providerMessageId or "",
                    "DELIVERED" if event == "message_delivered" else "READ",
                    payload.error,
                )
        except Exception as delivery_error:
            logger.warning("QR delivery status update failed: %s", delivery_error)
            return {"status": "ignored", "reason": "delivery_status_failed"}
        return {"status": "ok"}
    if event == "logged_out":
        if row.status != WhatsAppConnectionStatus.LOGGED_OUT:
            row.status = WhatsAppConnectionStatus.LOGGED_OUT
            row.disconnected_at = now
            await db.commit()
            await _alert_whatsapp_unlinked(db, row)
        return {"status": "ok"}
    if event == "connected":
        row.status = WhatsAppConnectionStatus.CONNECTED
        row.connected_at = row.connected_at or now
        row.last_seen_at = now
        await db.commit()
        await _resolve_whatsapp_alerts(db, row)
        return {"status": "ok"}
    return {"status": "ignored", "reason": "unknown_event"}


@router.post("/internal/whatsapp-qr/inbound")
async def qr_inbound(    payload: QrInboundPayload,
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
    # Exact text the human sent, captured before any directive-tap rewrite.
    # Booking logic consumes the (possibly rewritten) `text`; the transcript
    # and audit trail must record what was actually received.
    _original_text = text
    _inbound_message_id = str(payload.messageId or "").strip() or None
    # Make the provider id ambient for every persistence call made while this
    # message is handled (directive-tap replies, engine transcript, cancel /
    # check-booking / decline helpers). Starlette runs each request in its own
    # task, so this value cannot leak into another conversation.
    try:
        from app.core.inbound_context import set_inbound_message_id

        set_inbound_message_id(_inbound_message_id)
    except Exception:
        pass
    if len(from_phone) < 7 or not text:
        release_message(f"qr:{payload.messageId}")
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
    # Flood accounting is enforced AFTER session load so a burst gets a
    # spoken slowdown notice, never silence.
    _rate_exempt = bool(is_otp_code or is_knowledge)

    from app.crud import chat as crud_chat
    from app.engine import runtime_router
    from app.core import whatsapp_client
    from app.models.config import OrgConfig

    cfg_res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    org_config = cfg_res.scalars().first()
    if org_config is None:  # pragma: no cover — every org has one
        release_message(f"qr:{payload.messageId}")
        return {"status": "ignored"}

    # Booking kill switch: replies work only when the admin toggle is ON.
    # Merely connecting the number never enables replies.
    _bk_enabled, _bk_active, _bk_reason = _booking_state(row)
    if not _bk_active:
        logger.info(
            "qr inbound ignored (whatsapp booking disabled: %s)",
            _bk_reason or "unknown",
        )
        release_message(f"qr:{payload.messageId}")
        return {"status": "ignored", "reason": _bk_reason or "whatsapp_booking_disabled"}

    chat_session = await crud_chat.get_or_create_channel_session(
        db, str(org.id), "whatsapp", from_phone
    )
    # Stash the full chat JID (+ push name) for admin→customer pushes.
    # channel_user_id holds digits only, which is unreliable for LID
    # identities — the stored JID always routes.
    try:
        _cd_jid = dict(chat_session.collected_data or {})
        if str(payload.chatJid or "").strip():
            _cd_jid["whatsapp_chat_jid"] = str(payload.chatJid).strip()
        if str(payload.pushName or "").strip():
            _cd_jid["pushName"] = str(payload.pushName).strip()[:128]
        chat_session.collected_data = _cd_jid
        await crud_chat.update_chat_session(db, str(chat_session.session_id), chat_session)
    except Exception:
        pass
    # Human handover: the admin owns this conversation — persist the
    # customer message for the inbox and send NOTHING back. (Directive-tap
    # guards below must not reply either.)
    try:
        if await crud_chat.is_human_takeover_active(db, str(chat_session.session_id)):
            try:
                await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
            except Exception:
                pass
            logger.info(
                "qr inbound human-active: AI silent, session=%s",
                str(chat_session.session_id)[:8],
            )
            return {"status": "ok", "session_id": str(chat_session.session_id), "reply": None}
    except Exception:
        pass
    # Flood protection (sliding window — steady pacing never trips it). A
    # burst gets a spoken slowdown notice with the transcript saved, never
    # silence: silent drops were reported as "agent stops replying".
    if not _rate_exempt:
        _limited, _retry_after = check_sender_rate_limit(f"qr:{from_phone}")
        if _limited:
            _wait = max(1, int(round(_retry_after)))
            _slow_body = (
                f"You're sending messages very quickly — please wait about "
                f"{_wait} seconds and send that again."
            )
            try:
                await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                await crud_chat.save_message(db, str(chat_session.session_id), "agent", _slow_body)
            except Exception:
                pass
            logger.warning("QR inbound burst slowed (retry in ~%ss)", _wait)
            return {"status": "ok", "session_id": str(chat_session.session_id), "reply": {"type": "text", "body": _slow_body[:4096]}}
    # Structured directive tap — handle without LLM if text matches active directive's option
    try:
        _active_id = None
        if isinstance(chat_session.collected_data, dict):
            _active_id = chat_session.collected_data.get("active_directive_id")
        if _active_id:
            from app.engine.booking_directive.service import get_by_id as _get_dir
            from app.engine.booking_directive import handler as _dir_handler

            _dir = await _get_dir(db, _active_id)
            # Availability/hours inquiries bypass directive interception
            # entirely: the engine answers from the live slot inventory (or
            # weekly hours), then re-asks the pending step. Without this, a
            # question like "tell me the available dates" is validated as
            # step input and the user is interrogated instead of answered.
            # Explicit button taps (optionId) always take precedence.
            _inquiry_bypass = False
            if not str(getattr(payload, "optionId", "") or "").strip():
                try:
                    from app.engine.scheduling_policy import (
                        is_available_dates_query as _is_dates_q,
                        is_schedule_business_hours_query as _is_hours_q,
                    )
                    if _is_dates_q(text) or _is_hours_q(text):
                        _inquiry_bypass = True
                        logger.info("Directive interception bypassed for inquiry (qr)")
                except Exception:
                    pass
            if _dir and _dir.status == "PENDING" and _dir.customer_id == from_phone and not _inquiry_bypass:
                # Shared strict step guards (both transports use this module).
                from app.engine import whatsapp_guards as _guards

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
                    # Objection handling FIRST (before step validation): a
                    # customer asking "why do you need this?" or saying "I
                    # don't want to book" gets acknowledged in the
                    # admin-chosen tone — never a format slap, never ignored.
                    # Explicit taps (oid) already won above.
                    if not _matched:
                        _dtype_o = str(getattr(_dir, "type", "") or "")
                        _step_field_o = {"SELECT_DATE": "date", "SELECT_SLOT": "time", "SELECT_TIME": "time"}.get(_dtype_o)
                        _cd_o = dict(chat_session.collected_data or {})
                        _objection = _guards.detect_objection(text, _cd_o, _step_field_o)
                        if _objection:
                            _brief = _guards.build_objection_brief(
                                category=str(_objection.get("category") or ""),
                                field=_objection.get("field"),
                                business_name=str(
                                    ((getattr(org_config, "custom_prompts", None) or {}).get("business_name"))
                                    or getattr(org, "name", "")
                                    or ""
                                ),
                                tone=(getattr(org_config, "custom_prompts", None) or {}).get("tone"),
                                field_label=_guards.field_slot(
                                    getattr(org_config, "field_definitions", None),
                                    _objection.get("field"),
                                )[0],
                                field_description=_guards.field_slot(
                                    getattr(org_config, "field_definitions", None),
                                    _objection.get("field"),
                                )[1],
                                field_required=_guards.field_slot(
                                    getattr(org_config, "field_definitions", None),
                                    _objection.get("field"),
                                )[2],
                                required_labels=_guards.required_labels(
                                    getattr(org_config, "field_definitions", None)
                                ),
                                step_title=str(getattr(_dir, "title", "") or ""),
                                step_prompt=str(getattr(_dir, "description", "") or ""),
                            )
                            try:
                                from app.engine.whatsapp_presenter import WhatsAppPresenter as _WPPresenter

                                _obj_resp = await _WPPresenter().present_objection(_brief)
                                _obj_body = str(_obj_resp.body or "").strip() or "Noted — how else can I help with your booking?"
                            except Exception as _obj_e:
                                logger.warning(f"Objection presentation failed, engine fallback: {_obj_e}")
                                _obj_body = ""
                            if _obj_body:
                                try:
                                    await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                                    await crud_chat.save_message(db, str(chat_session.session_id), "agent", _obj_body)
                                except Exception:
                                    pass
                                logger.info(f"Directive {str(_dir.id)[:8]} objection acknowledged ({_brief.get('category')}): {text[:40]!r}")
                                return {"status": "ok", "session_id": str(chat_session.session_id), "reply": {"type": "text", "body": _obj_body[:4096]}}
                            # Presentation failed and produced nothing: fall
                            # through to normal validation/engine below.
                    if not _matched:
                        # Strict step guards: exact option text always;
                        # positional index ONLY for time slots (times are
                        # commonly picked by number). Bare numbers are NEVER a
                        # date — simple-text prompts show no numbers.
                        _dtype_m = str(getattr(_dir, "type", "") or "")
                        _opts_m = list(_dir.options or [])
                        _matched = _guards.match_option_by_text(_opts_m, text)
                        if (
                            not _matched
                            and _dtype_m in ("SELECT_SLOT", "SELECT_TIME")
                        ):
                            _matched = _guards.match_slot_index(_opts_m, text)
                        # Flexible time match: "3 pm" / "3pm" / "3:00 pm" all
                        # mean the "3:00 PM" slot. Compare by hour/minute.
                        if not _matched and str(getattr(_dir, "type", "")) in ("SELECT_SLOT", "SELECT_TIME"):
                            try:
                                from app.engine.time_flex import match_time_to_slot_label as _match_slot

                                _labels = [str(o.get("label") or "") for o in (_dir.options or [])]
                                _hit = _match_slot(text, _labels)
                                if _hit:
                                    for _opt in (_dir.options or []):
                                        if str(_opt.get("label") or "") == _hit:
                                            _matched = _opt
                                            break
                            except Exception:
                                pass
                        # Flexible confirm match (typo-tolerant: "Comfirm").
                        if not _matched and str(getattr(_dir, "type", "")) == "CONFIRM_BOOKING":
                            try:
                                from app.engine.time_flex import is_confirm_message as _is_confirm

                                if _is_confirm(text):
                                    for _opt in (_dir.options or []):
                                        _oid_l = str(_opt.get("id") or "").lower()
                                        if "confirm" in _oid_l or str(_opt.get("value") or "").lower() == "confirm":
                                            _matched = _opt
                                            break
                            except Exception:
                                pass
                    # Strict typed-date acceptance (SELECT_DATE only): a valid
                    # in-horizon date completes the step even when it is not
                    # one of the listed options. Anything else gets an
                    # exact-format correction — the bot never guesses.
                    _typed_iso = None
                    # True when this turn is handed to the engine untouched
                    # (cancel bypass / cross-field change) — guards below skip.
                    _step_handled = False
                    if not _matched and str(getattr(_dir, "type", "")) == "SELECT_DATE":
                        _tz_g = str(getattr(org_config, "timezone", None) or "UTC")
                        try:
                            from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                            _max_g = (await _resolve_horizon(db, org.id, org_config))[0]
                        except Exception:
                            _max_g = 30
                        _iso_g, _corr_g, _reason_g = _guards.validate_typed_date(
                            text,
                            tz=_tz_g,
                            max_days=_max_g,
                            examples=_guards.example_date_hints(
                                [str(o.get("value") or "") for o in (_dir.options or [])],
                                tz=_tz_g,
                                max_days=_max_g,
                            ),
                        )
                        if _iso_g:
                            _available_g, _availability_correction, _availability_reason = await _guards.validate_typed_date_availability(
                                db,
                                org.id,
                                org_config,
                                _iso_g,
                            )
                            if not _available_g:
                                _iso_g = None
                                _corr_g = _availability_correction
                                _reason_g = _availability_reason
                        if _iso_g:
                            _typed_iso = _iso_g
                            _matched = {
                                "id": f"date_typed_{_iso_g.replace('-', '')}",
                                "label": _iso_g,
                                "value": _iso_g,
                            }
                        else:
                            # Not a date value: let the LLM decide. Lookup /
                            # cancel / change / question turns must reach the
                            # engine instead of being trapped by a date-format
                            # correction. Pure LLM judgment — no phrase lists.
                            _bypass = False
                            try:
                                from app.engine import check_booking_flow as _check_flow

                                _bypass = await _check_flow.should_bypass_step_guard(text)
                            except Exception:
                                _bypass = False
                            if _bypass:
                                _dir.status = "EXPIRED"
                                _cd_b = dict(chat_session.collected_data or {})
                                _cd_b.pop("active_directive_id", None)
                                chat_session.collected_data = _cd_b
                                try:
                                   await crud_chat.update_chat_session(
                                        db,
                                        str(chat_session.session_id),
                                        chat_session,
                                        remove_keys=("active_directive_id",),
                                    )
                                except Exception:
                                    pass
                                logger.info(f"Directive {str(_dir.id)[:8]} LLM bypass to engine: {text[:40]!r}")
                                _step_handled = True
                            else:
                                _horizon_g = str(getattr(_dir, "description", "") or "").strip()
                                _body = _guards.date_correction(text, _corr_g or "", _horizon_g)
                                try:
                                    await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                                    await crud_chat.save_message(db, str(chat_session.session_id), "agent", _body)
                                except Exception:
                                    pass
                                logger.info(f"Directive {str(_dir.id)[:8]} wrong-date corrected ({_reason_g}): {text[:40]!r}")
                                return {"status": "ok", "session_id": str(chat_session.session_id), "reply": {"type": "text", "body": _body[:4096]}}
                    # Change-intent on CONFIRM_BOOKING ("change my date"): map to
                    # the change option like the typo-tolerant confirm mapping.
                    if not _matched and str(getattr(_dir, "type", "")) == "CONFIRM_BOOKING":
                        try:
                            if _guards.match_confirm_intent(text) == "change":
                                for _opt in (_dir.options or []):
                                    _oid_l = str(_opt.get("id") or "").lower()
                                    if "change" in _oid_l or str(_opt.get("value") or "").lower() == "change":
                                        _matched = _opt
                                        break
                        except Exception:
                            pass
                    # Cancel bypass + cross-field change routing: these belong
                    # to the engine (cancel flow / collected-field overrides),
                    # never to step validation. Expire the stale step, apply
                    # pops, and fall through with the ORIGINAL text so the
                    # engine sees the full request (values included).
                    if not _matched:
                        _dtype_h = str(getattr(_dir, "type", "") or "")
                        _step_field = {"SELECT_DATE": "date", "SELECT_SLOT": "time", "SELECT_TIME": "time"}.get(_dtype_h)
                        _cd_h = dict(chat_session.collected_data or {})
                        if _guards.is_cancel_bypass(text):
                            _dir.status = "EXPIRED"
                            _cd_h.pop("active_directive_id", None)
                            chat_session.collected_data = _cd_h
                            try:
                                await crud_chat.update_chat_session(
                                    db,
                                    str(chat_session.session_id),
                                    chat_session,
                                    remove_keys=("active_directive_id",),
                                )
                            except Exception:
                                pass
                            logger.info(f"Directive {str(_dir.id)[:8]} cancel bypass to engine: {text[:40]!r}")
                            _step_handled = True
                        else:
                            _change_field = _guards.detect_field_change_request(
                                text, _cd_h, _step_field
                            )
                            if _change_field:
                                _dir.status = "EXPIRED"
                                _cd_h = _guards.apply_collected_field_change(_cd_h, _change_field)
                                _cd_h.pop("active_directive_id", None)
                                chat_session.collected_data = _cd_h
                                try:
                                   await crud_chat.update_chat_session(
                                        db,
                                        str(chat_session.session_id),
                                        chat_session,
                                        remove_keys=("active_directive_id",),
                                    )
                                except Exception:
                                    pass
                                logger.info(f"Directive {str(_dir.id)[:8]} field-change ({_change_field}) to engine: {text[:40]!r}")
                                _step_handled = True
                    # Wrong-slot guard: an active time picker exists but the
                    # text matches NOTHING. Deterministic format correction —
                    # no numbers (simple-text prompts show none), never the LLM.
                    if not _matched and not _step_handled and str(getattr(_dir, "type", "")) in ("SELECT_SLOT", "SELECT_TIME", "SELECT_DATE"):
                        # LLM bypass first: lookup/cancel/change/questions go
                        # to the engine, never into a format correction.
                        _llm_bypass2 = False
                        try:
                            from app.engine import check_booking_flow as _check_flow2

                            _llm_bypass2 = await _check_flow2.should_bypass_step_guard(text)
                        except Exception:
                            _llm_bypass2 = False
                        if _llm_bypass2:
                            _dir.status = "EXPIRED"
                            _cd_b2 = dict(chat_session.collected_data or {})
                            _cd_b2.pop("active_directive_id", None)
                            chat_session.collected_data = _cd_b2
                            try:
                                await crud_chat.update_chat_session(
                                    db,
                                    str(chat_session.session_id),
                                    chat_session,
                                    remove_keys=("active_directive_id",),
                                )
                            except Exception:
                                pass
                            logger.info(f"Directive {str(_dir.id)[:8]} LLM bypass to engine (slot guard): {text[:40]!r}")
                            _step_handled = True
                    if not _matched and not _step_handled and str(getattr(_dir, "type", "")) in ("SELECT_SLOT", "SELECT_TIME", "SELECT_DATE"):
                        _dtype = str(getattr(_dir, "type", "") or "")
                        if _dtype == "CONFIRM_BOOKING":
                            _body = _guards.confirm_correction()
                        elif _dtype == "SELECT_DATE":
                            _horizon2 = str(getattr(_dir, "description", "") or "").strip()
                            _tz2 = str(getattr(org_config, "timezone", None) or "UTC")
                            try:
                                from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                                _max2 = (await _resolve_horizon(db, org.id, org_config))[0]
                            except Exception:
                                _max2 = 30
                            _ex2 = _guards.example_date_hints(
                                [str(o.get("value") or "") for o in (_dir.options or [])],
                                tz=_tz2,
                                max_days=_max2,
                            )
                            _hint2 = " or ".join(f"*{h}*" for h in _ex2) if _ex2 else "the day, month and year in full"
                            _body = _guards.date_correction(
                                text,
                                f"Please type the meeting date like {_hint2}.",
                                _horizon2,
                            )
                        else:
                            _avail = [str(o.get("label") or "").strip() for o in (_dir.options or []) if str(o.get("label") or "").strip()]
                            _hours = ""
                            if _avail:
                                _hours = "Available times: " + " · ".join(_avail[:7]) + "."
                            _body = _guards.time_correction(
                                text, _hours, examples=_guards.example_time_hints(_avail)
                            )
                        try:
                            await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                            await crud_chat.save_message(db, str(chat_session.session_id), "agent", _body)
                        except Exception:
                            pass
                        logger.info(f"Directive {str(_dir.id)[:8]} wrong-slot corrected: {text[:40]!r}")
                        return {"status": "ok", "session_id": str(chat_session.session_id), "reply": {"type": "text", "body": _body[:4096]}}
                    if _matched:
                        if _typed_iso:
                            # Synthetic typed date: verify() would reject the
                            # unknown option id, so complete directly (same
                            # trust level — text already strictly validated).
                            try:
                                from app.engine.booking_directive.service import complete as _complete_dir

                                _syn = {
                                    "id": f"date_typed_{_typed_iso.replace('-', '')}",
                                    "label": _typed_iso,
                                    "value": _typed_iso,
                                }
                                await _complete_dir(db, _dir.id, _syn["id"], booking_id=None)
                                try:
                                    _row2 = await _get_dir(db, _dir.id)
                                    if _row2 is not None:
                                        _row2.selected_option = _syn
                                        await db.flush()
                                except Exception:
                                    pass
                                _cd2 = dict(chat_session.collected_data or {})
                                _cd2["date"] = _typed_iso
                                _cd2.pop("active_directive_id", None)
                                chat_session.collected_data = _cd2
                                from app.engine.evaluator import StateEvaluator as _SE2
                                from app.schemas.agent import parse_field_definitions as _pfd2, order_field_definitions as _ofd2

                                _fds2 = _ofd2(_pfd2(chat_session.collected_data.get("field_definitions_snapshot") or org_config.field_definitions or org_config.required_fields))
                                _req2 = [f.name for f in _fds2 if f.required]
                                chat_session.missing_fields = _SE2.calculate_missing_fields(_req2, _cd2)
                                await crud_chat.update_chat_session(
                                    db,
                                    str(chat_session.session_id),
                                    chat_session,
                                    remove_keys=("active_directive_id",),
                                )
                                logger.info(f"Directive {str(_dir.id)[:8]} completed via typed date: {_typed_iso}")
                            except Exception as _e2:
                                logger.warning(f"Directive typed-date completion failed: {_e2}")
                            text = _typed_iso
                            _res = {"ok": True}
                        else:
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
                                    await crud_chat.update_chat_session(
                                        db,
                                        str(chat_session.session_id),
                                        chat_session,
                                        remove_keys=("active_directive_id",),
                                    )
                                    logger.info(f"Directive {str(_dir.id)[:8]} completed via tap: {str(_matched.get('label'))[:20]}")
                            except Exception as _e:
                                logger.warning(f"Directive tap state update failed: {_e}")
                            # Generate next directive or proceed to booking via normal engine on next turn
                            # For now, let next turn's engine handle next step — return a short ack
                            # But to avoid an extra round-trip, we can directly invoke the engine for the next step
                            # Fall through to normal engine with the selected value as message_text.
                            # CHANGE keeps the ORIGINAL text so a new value inside
                            # ("change my phone to 98...") reaches extraction.
                            _is_change_opt = "change" in str((_matched or {}).get("id") or "").lower() or str((_matched or {}).get("value") or "").lower() == "change"
                            if not _is_change_opt:
                                text = str(_matched.get("value") or _matched.get("label") or text)
    except Exception as _e:
        logger.warning(f"Directive tap handling failed, falling back to LLM: {_e}")

    logger.info("qr inbound session=%s (org resolved, engine next)", chat_session.session_id)
    session_lock = get_session_lock(session_lock_key(str(chat_session.session_id)))
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
                transcript_user_text=_original_text,
                inbound_message_id=_inbound_message_id,
            )
        except Exception:
            logger.exception("qr inbound engine raised")
            release_message(f"qr:{payload.messageId}")
            raise

        # Off-topic strike accounting, applied here because the engine has
        # now returned and the reply is final.
        #
        # Accounting inside the orchestrator did not work: the declines
        # customers actually receive are produced by the presenter/LLM layer
        # on paths that return before the orchestrator's own checks, so a live
        # test produced three clean declines and recorded zero strikes. Doing
        # it after the engine returns is the only point every path passes
        # through, and it is the last point before the message is sent.
        try:
            from app.crud import chat as _crud_chat
            from app.engine import offtopic_cooldown as _cd

            # Judge the reply by what it DOES, not by exact wording. The model
            # phrases the same refusal several ways and an equality check on one
            # canonical string missed most of them, so no strike was ever
            # recorded and the cooldown never opened.
            _reply_text = str(getattr(action, "message", "") or "")
            # A session that just booked (or just verified) is never punished:
            # its confirmation and OTP replies must reach the customer
            # untouched. This is the second half of the fix for confirmations
            # being overwritten by the cooldown notice while the booking
            # itself succeeded. The classifier also excludes these shapes, so
            # this is defence in depth, not the primary guard.
            try:
                from app.engine.user_visible_text import (
                    looks_like_confirmation_claim as _is_claim,
                )
                from app.engine.knowledge_guards import (
                    is_transactional_reply as _is_txn,
                )

                _just_booked = str(
                    getattr(chat_session, "session_status", "") or ""
                ).upper() in ("CONFIRMING", "CONFIRMED") or bool(
                    getattr(chat_session, "phone_otp_verified", False)
                )
                # A turn that IS booking/business progress is never a strike,
                # even when the reply looks decline-shaped. The engine knew
                # the turn-3 booking request was START_BOOKING, yet its
                # clarification reply ("I didn't quite catch that... share
                # your full name") was judged a decline and locked the
                # customer out for trying to book.
                _on_topic_turn = _cd.is_on_topic_turn(
                    text, getattr(chat_session, "current_intent", None)
                ) or bool(getattr(chat_session, "missing_fields", None))
            except Exception:
                _just_booked = False
                _on_topic_turn = False
                _is_claim = None
                _is_txn = None
            if _just_booked or _on_topic_turn or (
                _is_claim is not None and _is_claim(_reply_text)
            ) or (
                _is_txn is not None and _is_txn(_reply_text)
            ):
                _cd.clear(chat_session.collected_data)
            elif _cd.looks_like_decline(_reply_text):
                if not _cd.looks_like_asking_about_us(text):
                    # If the window was not open before this turn, this strike
                    # just opened it: the customer hears the plain opening
                    # line, not another decline. Anything they send after
                    # this gets the "still in cooldown" line from the early
                    # gate instead.
                    _already_cooling = _cd.is_cooling_down(
                        chat_session.collected_data
                    )
                    _cd.record_offtopic(chat_session.collected_data)
                    if (
                        not _already_cooling
                        and _cd.is_cooling_down(chat_session.collected_data)
                    ):
                        action.message = _cd.cooldown_opening_message()
                        logger.info(
                            "offtopic_cooldown_opened session=%s",
                            str(chat_session.session_id)[:8],
                        )
            else:
                _cd.clear(chat_session.collected_data)
            await _crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
        except Exception:
            logger.warning("off-topic accounting failed", exc_info=True)

        _dt = _time.monotonic() - _t0
        # Human handover won the race after the early check, or the engine
        # stayed silent on empty input: send nothing back.
        if str(getattr(action, "widget_directive", "") or "") in ("HUMAN_ACTIVE", "SILENT_NO_REPLY"):
            return {"status": "ok", "session_id": str(chat_session.session_id), "reply": None}
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
                from app.engine.booking_directive.service import (
                    directive_frame_matches_action as _frame_matches,
                )

                _dir_after = await _get_dir2(db, _active_id_after)
                if (
                    _dir_after
                    and _dir_after.status == "PENDING"
                    and _frame_matches(
                        getattr(_dir_after, "type", ""),
                        getattr(action, "widget_directive", ""),
                    )
                ):
                    _title = str(getattr(_dir_after, "title", "") or "").strip()
                    _desc = str(getattr(_dir_after, "description", "") or "").strip()
                    _opts = list(getattr(_dir_after, "options", None) or [])
                    # If directive description empty (old rows), generate horizon/hours on the fly for smooth text
                    if not _desc:
                        try:
                            if _dir_after.type == "SELECT_DATE":
                                from app.engine.message_processor import _format_available_dates_for_prompt
                                _tz_q = str(getattr(org_config, "timezone", None) or "UTC")
                                from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                                _max_q = (await _resolve_horizon(db, org.id, org_config))[0]
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
                    # For time, show available slots as plain text (typed entry, not numbered buttons)
                    if _dir_after.type in ("SELECT_SLOT", "SELECT_TIME"):
                        _opts = list(getattr(_dir_after, "options", None) or [])
                        if _opts:
                            _labels = [str(o.get('label') or '').strip() for o in _opts[:7] if o.get('label')]
                            if _labels:
                                _parts.append("Available slots: " + " · ".join(_labels))
                                _parts.append("You can type any format — e.g. 3pm, 3 pm or 3:00 PM.")
                    # Confirm step must show the booking summary + explicit
                    # reply options. The old text-only render sent a bare
                    # "Please confirm your booking" with no CONFIRM/CHANGE
                    # hint, so users typed "Okay"/typos and looped.
                    if _dir_after.type == "CONFIRM_BOOKING":
                        _summary = str(getattr(action, "message", "") or "").strip()
                        if _summary and _summary not in _parts:
                            _parts.append(_summary)
                        _parts.append("Reply CONFIRM to confirm or CHANGE to modify.")
                    _body = "\n\n".join(_parts).strip()
                    # Knowledge answers must not die behind the step render:
                    # mid-booking questions are answered AND the pending step
                    # stays visible (answer first, step second).
                    try:
                        _fm_after = getattr(action, "field_meta", None) or {}
                        if isinstance(_fm_after, dict) and _fm_after.get("knowledge_answer"):
                            _body = whatsapp_client.attach_knowledge_answer(
                                getattr(action, "message", ""), _body
                            )
                    except Exception:
                        pass
                    wa_msg = {"type": "text", "body": _body[:4096]}
                    logger.info(f"Rendered simple-text directive type={_dir_after.type} directive={str(_dir_after.id)[:8]}")
            except Exception as _e:
                logger.warning(f"Directive render failed, falling back: {_e}")
        if wa_msg is None:
            # Non-directive path — keep deterministic booking engine rendering for now.
            # Knowledge-tone presentation for ASK_QUESTION will be handled via graph's RAG path (already tone-aware);
            # presenter for text is reserved for Phase 5 when we wire retrieved chunks explicitly.
            wa_msg = whatsapp_client.format_action_for_whatsapp(action)
            # Final anti-hallucination gate: block invented contact/money
            # entities (phones, emails, prices) absent from org facts, catalog
            # fees, and the customer's own session details. Fail-open on error.
            # Skip for deterministic duplicate-booking (DB truth, not hallucination)
            _skip_gate_qr = False
            try:
                _fm_qr = getattr(action, "field_meta", None) or {}
                if isinstance(_fm_qr, dict) and _fm_qr.get("duplicate_booking"):
                    _skip_gate_qr = True
            except Exception:
                pass
            if not _skip_gate_qr:
                try:
                    from app.engine import knowledge_guards as _kb_guards

                    _body0 = str((wa_msg or {}).get("body") or "")
                    if _body0 and str((wa_msg or {}).get("type") or "") == "text":
                        _cd0 = dict(chat_session.collected_data or {})
                        _facts0: list = [str(getattr(org, "name", "") or "")]
                        try:
                            from app.engine.knowledge_profile import format_business_profile as _fbp

                            _facts0.append(_fbp(getattr(org_config, "knowledge_context", None)) or "")
                        except Exception:
                            pass
                        try:
                            from sqlalchemy.future import select as _SelFee
                            from app.models.service_unit import ProviderFee as _FeeModel

                            _fees = (
                                await db.execute(
                                    _SelFee(_FeeModel).where(
                                        _FeeModel.org_id == org.id,
                                        _FeeModel.is_active.is_(True),
                                    )
                                )
                            ).scalars().all()
                            for _f in _fees:
                                if _f.amount_paise:
                                    _facts0.append(f"{(_f.currency or 'INR')} {(_f.amount_paise / 100):,.2f}")
                        except Exception:
                            pass
                        _sess0 = [str(_cd0.get(k) or "") for k in ("name", "email", "phone", "date", "time")]
                        _safe0, _blocked0, _ents0 = _kb_guards.guard_final_text(
                            _body0, org_facts=_facts0, session_details=_sess0
                        )
                        if _blocked0:
                            logger.warning("whatsapp KB entity gate blocked reply with unlisted %s", _ents0)
                            # Do not save second ghost row — transcript already saved by
                            # langgraph_orchestrator; saving here creates divergence
                            # (Image 1 ghost only on WhatsApp, transcript shows duplicate+ghost)
                            wa_msg = {"type": "text", "body": _safe0[:4096]}
                except Exception as _gate_e:
                    logger.warning(f"whatsapp KB entity gate failed open: {_gate_e}")
        # Approved Marketing templates via Cloud API (language `en`), Baileys stays as fallback.
        # Sends Cloud copy for Meta compliance; the Baileys `wa_msg` above is still
        # returned so the QR thread keeps working (dual-sender, same as OTP today).
        try:
            from app.core import whatsapp_templates as _wtemplates

            if _wtemplates.templates_enabled():
                _directive = getattr(action, "widget_directive", "") or ""
                _fmeta = getattr(action, "field_meta", None) or {}
                _sdata = chat_session.collected_data if isinstance(getattr(chat_session, "collected_data", None), dict) else {}
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
        # Repeat circuit breaker: identical consecutive replies get a
        # one-time escape hatch instead of looping forever.
        try:
            _body = str((wa_msg or {}).get("body") or "")
            if _body.strip():
                _broken = await whatsapp_client.break_identical_repeat(
                    db, chat_session, _body)
                if _broken != _body:
                    wa_msg = dict(wa_msg or {})
                    wa_msg["body"] = _broken[:4096]
        except Exception as _brk_e:
            logger.warning(f"repeat breaker failed open: {_brk_e}")
        logger.info(
            "qr inbound engine done in %.1fs reply_type=%s reply_len=%s",
            _dt,
            (wa_msg or {}).get("type"),
            len(str((wa_msg or {}).get("body") or "")),
        )
    logger.info("qr inbound processed org=%s session=%s", org.id, chat_session.session_id)
    return {"status": "ok", "session_id": str(chat_session.session_id), "reply": wa_msg}
