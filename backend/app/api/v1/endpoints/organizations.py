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
from app.schemas.audit import AuditAction
import uuid

router = APIRouter()

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
    
    session = await crud_chat.set_human_takeover(db, str(conversation_id), str(user.id), user.full_name)
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
    
    session = await crud_chat.release_human_control(db, str(conversation_id))
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
