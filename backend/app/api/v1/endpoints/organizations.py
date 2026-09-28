from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from pydantic import UUID4, BaseModel
from typing import Optional, List

from app.db.database import get_db
from app.crud import organization as crud_org
from app.crud import chat as crud_chat
from app.schemas.organization import OrganizationResponse, OrganizationCreate, RotateApiKeyRequest, RotateApiKeyResponse
from app.models.user import User
from app.models.session import ChatSession
from app.models.message import ChatMessage
from app.api.v1.endpoints.auth import get_current_user
from app.core.audit import log_audit_event
from app.core.ws_manager import ws_manager
from app.schemas.audit import AuditAction
import logging
import uuid

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    org_in: OrganizationCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await crud_org.create_organization(db, org_in)
    log_audit_event(
        background_tasks,
        org_id=org.id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.ORG_CREATE,
        resource_type="organization",
        resource_id=str(org.id),
        old_values=None,
        new_values={"name": org.name, "is_active": org.is_active},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return org


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: UUID4,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")
    org = await crud_org.get_organization(db, str(org_id))
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.post("/{org_id}/rotate-api-key", response_model=RotateApiKeyResponse)
async def rotate_organization_api_key(
    org_id: UUID4,
    request_body: RotateApiKeyRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")
    try:
        org = await crud_org.rotate_api_key(
            db, str(org_id), request_body.grace_period_days
        )
        log_audit_event(
            background_tasks,
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            action=AuditAction.APIKEY_ROTATE,
            resource_type="organization",
            resource_id=str(org_id),
            old_values={"grace_period_days": request_body.grace_period_days},
            new_values={"old_key_expires_at": org.old_key_expires_at.isoformat()},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return RotateApiKeyResponse(
            new_api_key=org.api_key,
            old_key_expires_at=org.old_key_expires_at,
            message=(
                "API key rotated. Old key valid until "
                f"{org.old_key_expires_at.isoformat()}"
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ... existing endpoints ...

# ── Human Handoff API ──────────────────────────────


class TakeoverResponse(BaseModel):
    success: bool
    conversation_id: str
    message: str
    previous_messages: List[dict] = []


class ReleaseResponse(BaseModel):
    success: bool
    conversation_id: str
    message: str


class SendHumanMessageRequest(BaseModel):
    content: str


@router.post("/{org_id}/conversations/{conversation_id}/takeover", response_model=TakeoverResponse)
async def takeover_conversation(
    org_id: UUID4,
    conversation_id: UUID4,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Admin takes control of a conversation (human handoff).
    """
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")

    # Verify conversation exists and belongs to org
    result = await db.execute(select(ChatSession).where(ChatSession.id == conversation_id))
    session = result.scalars().first()
    if not session or str(session.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    session = await crud_chat.set_human_takeover(db, str(conversation_id), user.org_id, str(user.id), user.full_name)
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Get previous messages for context
    messages = await crud_chat.get_session_messages(db, str(conversation_id), limit=50)
    previous_messages = [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "sender_type": getattr(m, "sender_type", "ai"),
            "human_agent_id": str(m.human_agent_id) if m.human_agent_id else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]
    
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.CONVERSATION_TAKEOVER,
        resource_type="conversation",
        resource_id=str(conversation_id),
        old_values={"human_agent_active": False},
        new_values={"human_agent_active": True, "human_agent_id": str(user.id)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Best-effort real-time broadcast to the customer widget (Phase 3+5).
    # Never raises — booking flow is unaffected if no widget is connected.
    try:
        await ws_manager.broadcast_to_session(
            str(conversation_id),
            {
                "type": "human_joined",
                "session_id": str(conversation_id),
                "admin_id": str(user.id),
                "admin_name": user.full_name,
            },
        )
    except Exception:
        pass

    return TakeoverResponse(
        success=True,
        conversation_id=str(conversation_id),
        message=f"{user.full_name} took over the conversation",
        previous_messages=previous_messages,
    )


@router.post("/{org_id}/conversations/{conversation_id}/release", response_model=ReleaseResponse)
async def release_conversation(
    org_id: UUID4,
    conversation_id: UUID4,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Admin releases control back to AI.
    """
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")

    # Verify conversation exists and belongs to org
    result = await db.execute(select(ChatSession).where(ChatSession.id == conversation_id))
    session = result.scalars().first()
    if not session or str(session.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    session = await crud_chat.release_human_control(db, str(conversation_id), user.org_id)
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.CONVERSATION_RELEASE,
        resource_type="conversation",
        resource_id=str(conversation_id),
        old_values={"human_agent_active": True},
        new_values={"human_agent_active": False},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Best-effort real-time broadcast to the customer widget (Phase 3+5).
    try:
        await ws_manager.broadcast_to_session(
            str(conversation_id),
            {
                "type": "human_left",
                "session_id": str(conversation_id),
            },
        )
    except Exception:
        pass

    return ReleaseResponse(
        success=True,
        conversation_id=str(conversation_id),
        message="Control released back to AI",
    )


@router.post("/{org_id}/conversations/{conversation_id}/send-human-message")
async def send_human_message(
    org_id: UUID4,
    conversation_id: UUID4,
    payload: SendHumanMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Admin sends a message as human agent.
    """
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")
    
    # Verify conversation exists and belongs to org
    result = await db.execute(select(ChatSession).where(ChatSession.id == conversation_id))
    session = result.scalars().first()
    
    if not session or str(session.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Save human message
    message = await crud_chat.save_human_message(db, str(conversation_id), str(user.id), payload.content)

    # Deliver to WhatsApp when the conversation lives there. Web delivery
    # stays on the existing WebSocket broadcast below.
    _channel = str(getattr(session, "channel", "") or "").lower()
    if _channel in ("whatsapp", "whatsapp_cloud"):
        try:
            from app.core import whatsapp_cloud as _cloud

            _to = str(getattr(session, "channel_user_id", "") or "")
            if _channel == "whatsapp_cloud":
                _res = await _cloud.send_cloud_text(_to, payload.content)
                logger.info(
                    "human message cloud send session=%s success=%s",
                    str(conversation_id)[:8], _res.get("success"),
                )
            else:
                from app.core import wa_gateway as _gw
                from app.models.whatsapp_connection import WhatsAppConnection as _Conn

                _conn_res = await db.execute(
                    select(_Conn).where(
                        _Conn.org_id == session.org_id
                    )
                )
                _conn = _conn_res.scalars().first()
                if _conn is not None:
                    _sdata = session.state_data if isinstance(getattr(session, "state_data", None), dict) else {}
                    _jid = str(_sdata.get("whatsapp_chat_jid") or "").strip()
                    if not _jid:
                        import re as _re

                        _digits = "".join(_re.findall(r"\d", _to))
                        _jid = f"{_digits}@s.whatsapp.net" if _digits else ""
                    if _jid:
                        _res = await _gw.send_session_message(
                            str(_conn.id), _jid, payload.content,
                            idem_key=f"human:{message.id}",
                        )
                        logger.info(
                            "human message QR send session=%s sent=%s",
                            str(conversation_id)[:8], _res.get("sent"),
                        )
                    else:
                        logger.warning(
                            "human message QR skipped (no recipient JID) session=%s",
                            str(conversation_id)[:8],
                        )
                else:
                    logger.warning(
                        "human message QR skipped (no connection) session=%s",
                        str(conversation_id)[:8],
                    )
        except Exception:
            logger.warning("human message WhatsApp delivery failed", exc_info=True)

    # Best-effort real-time broadcast to the customer widget (Phase 3+5).
    try:
        await ws_manager.broadcast_to_session(
            str(conversation_id),
            {
                "type": "human_message",
                "session_id": str(conversation_id),
                "message_id": str(message.id),
                "content": message.content,
                "sender_type": "human",
                "admin_id": str(user.id),
                "admin_name": user.full_name,
                "created_at": (
                    message.created_at.isoformat() if message.created_at else None
                ),
            },
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": {
            "id": str(message.id),
            "content": message.content,
            "sender_type": "human",
            "admin_name": user.full_name,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        }
    }
