"""WhatsApp QR pairing endpoints (Baileys transport via wa-gateway).

Every route resolves the connection row by the authenticated user's org_id —
the frontend never supplies an organization or connection id, so one org can
never touch another org's QR, session, or credentials. Write operations
additionally require a non-impersonated session.
"""

import hmac
import logging
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


class QrResponse(BaseModel):
    qr_data_url: str
    issued_at: int
    expires_at: int


def _snapshot(row: Optional[WhatsAppConnection]) -> ConnectionSnapshot:
    if row is None:
        return ConnectionSnapshot(status="NOT_CONNECTED")
    return ConnectionSnapshot(
        status=row.status,
        phone_number=row.phone_number,
        display_name=row.display_name,
        connected_at=row.connected_at.isoformat() if row.connected_at else None,
        disconnected_at=row.disconnected_at.isoformat() if row.disconnected_at else None,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
    )


async def _get_row(db: AsyncSession, org_id: object) -> Optional[WhatsAppConnection]:
    res = await db.execute(
        select(WhatsAppConnection).where(WhatsAppConnection.org_id == org_id)  # type: ignore[arg-type]
    )
    return res.scalars().first()


def _apply_gateway_status(row: WhatsAppConnection, gw: dict) -> None:
    """Merge live socket truth into the row (identity + mapped status)."""
    gw_status = str(gw.get("status") or "")
    if gw.get("phoneNumber"):
        row.phone_number = str(gw["phoneNumber"])[:32]
    if gw.get("displayName"):
        row.display_name = str(gw["displayName"])[:255]
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


# ── Internal inbound from wa-gateway (service-key auth, no JWT) ──────────

class QrInboundPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connectionId: str
    messageId: str
    from_number: str = Field(alias="from")
    chatJid: str
    text: str
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

    chat_session = await crud_chat.get_or_create_channel_session(
        db, str(org.id), "whatsapp", from_phone
    )
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
        wa_msg = whatsapp_client.format_action_for_whatsapp(action)
        logger.info(
            "qr inbound engine done in %.1fs reply_type=%s reply_len=%s",
            _dt,
            (wa_msg or {}).get("type"),
            len(str((wa_msg or {}).get("body") or "")),
        )
    logger.info("qr inbound processed org=%s session=%s", org.id, chat_session.session_id)
    return {"status": "ok", "session_id": str(chat_session.session_id), "reply": wa_msg}
