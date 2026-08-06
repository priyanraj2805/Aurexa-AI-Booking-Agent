"""Support endpoints (Phase 8) — read-only conversation peek with PII masking.

Reads flow through the read-only DB pool (SELECT-only role); transcripts are
masked before they leave the API. Every peek is audit-logged.
"""
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.pii import mask_dict, mask_text, mask_value
from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db, get_readonly_db

router = APIRouter()

SESSION_COLUMNS = (
    "id, channel, session_status, current_intent, human_agent_active, "
    "created_at, updated_at"
)


@router.get("/orgs/{org_id}/conversations")
async def list_conversations(
    org_id: UUID,
    unmask: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    rdb: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    can_unmask = unmask and admin.has_permission("pii.unmask")
    exists = (
        await rdb.execute(
            text("SELECT 1 FROM organizations WHERE id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    offset = (page - 1) * page_size
    total = (
        await rdb.execute(
            text("SELECT count(*) FROM chat_sessions WHERE org_id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    rows = (
        await rdb.execute(
            text(
                f"""
                SELECT {SESSION_COLUMNS},
                       channel_user_id,
                       (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
                FROM chat_sessions s
                WHERE s.org_id = :org_id
                ORDER BY s.updated_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"org_id": org_id, "limit": page_size, "offset": offset},
        )
    ).mappings().all()
    items = [
        {
            "id": str(r["id"]),
            "channel": r["channel"],
            "channel_user_id": (
                r["channel_user_id"]
                if can_unmask and r["channel_user_id"]
                else (
                    mask_value("channel_user_id", r["channel_user_id"])
                    if r["channel_user_id"]
                    else None
                )
            ),
            "session_status": r["session_status"],
            "current_intent": r["current_intent"],
            "human_agent_active": r["human_agent_active"],
            "message_count": r["message_count"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]
    return {"total": total or 0, "page": page, "page_size": page_size, "items": items}


@router.get("/orgs/{org_id}/conversations/{session_id}/messages")
async def peek_conversation(
    org_id: UUID,
    session_id: UUID,
    unmask: bool = Query(False),
    rdb: AsyncSession = Depends(get_readonly_db),
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    can_unmask = unmask and admin.has_permission("pii.unmask")
    session = (
        await rdb.execute(
            text(
                f"""
                SELECT {SESSION_COLUMNS}, channel_user_id, state_data
                FROM chat_sessions
                WHERE id = :session_id AND org_id = :org_id
                """
            ),
            {"session_id": session_id, "org_id": org_id},
        )
    ).mappings().first()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = (
        await rdb.execute(
            text(
                """
                SELECT id, role, content, sender_type, created_at
                FROM chat_messages
                WHERE session_id = :session_id
                ORDER BY created_at ASC
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().all()

    masked_state = {}
    if session["state_data"]:
        try:
            raw = json.loads(session["state_data"])
            masked_state = raw if isinstance(raw, dict) and can_unmask else (
                mask_dict(raw) if isinstance(raw, dict) else raw
            )
        except Exception:
            masked_state = {"_masked": True}

    await write_audit_log(
        actor_email=admin.email,
        action="support.conversation.peek",
        resource_type="conversation",
        resource_id=str(session_id),
        new_values={
            "org_id": str(org_id),
            "message_count": len(messages),
            "unmasked": can_unmask,
        },
    )

    return {
        "session_id": str(session["id"]),
        "channel": session["channel"],
        "channel_user_id": (
            session["channel_user_id"]
            if can_unmask and session["channel_user_id"]
            else (
                mask_value("channel_user_id", session["channel_user_id"])
                if session["channel_user_id"]
                else None
            )
        ),
        "session_status": session["session_status"],
        "state_data": masked_state,
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"] if can_unmask else mask_text(m["content"]),
                "sender_type": m["sender_type"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
    }
