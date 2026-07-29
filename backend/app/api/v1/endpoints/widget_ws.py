"""
Customer Widget WebSocket — real-time delivery layer for the chat widget.

The widget connects to this endpoint on page load (after restoring a session).
The endpoint authenticates the widget_token, resolves the org + session, and
registers the connection in ws_manager.customer_connections.

Once connected, the widget receives three event types:
    - human_joined     { admin_id, admin_name }
    - human_left       { }
    - human_message    { id, content, admin_name, created_at }

The widget still uses the existing GET /widget/history endpoint for initial
load and refresh; this WS is purely a real-time delivery channel.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.ws_manager import ws_manager
from app.db.database import AsyncSessionLocal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.session import ChatSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["widget-ws"])


async def _resolve_session(
    widget_token: str, session_id: str
) -> Optional[tuple[Organization, ChatSession]]:
    """
    Validate widget_token, find the org, and confirm the session belongs to it.
    Returns (org, session) or None if any check fails.
    """
    try:
        async with AsyncSessionLocal() as db:
            cfg_result = await db.execute(
                select(OrgConfig).where(OrgConfig.widget_token == widget_token)
            )
            cfg = cfg_result.scalars().first()
            if not cfg:
                return None

            org_result = await db.execute(
                select(Organization).where(Organization.id == cfg.org_id)
            )
            org = org_result.scalars().first()
            if not org or not org.is_active:
                return None

            sess_result = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_id,
                    ChatSession.org_id == org.id,
                )
            )
            session = sess_result.scalars().first()
            if not session:
                return None
            return (org, session)
    except Exception as exc:
        logger.warning(
            "widget_ws.resolve_session_failed",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return None


@router.websocket("/ws/widget")
async def widget_websocket(
    websocket: WebSocket,
    widget_token: str = Query(...),
    session_id: str = Query(...),
):
    """
    Customer widget WebSocket endpoint.

    Auth: widget_token (query param) — same token used by GET /widget/*.
    The connection is scoped to a single chat session_id.
    """
    resolved = await _resolve_session(widget_token, session_id)
    if resolved is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org, _session = resolved

    await ws_manager.connect_customer(session_id, websocket)

    # Send a one-time hello so the widget can confirm the channel is live.
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": session_id,
                "org_id": str(org.id),
            }
        )
    except Exception:
        ws_manager.disconnect_customer(session_id, websocket)
        return

    try:
        while True:
            # The widget doesn't need to send anything; we just keep the
            # channel alive by awaiting messages. Anything the widget sends
            # is logged but ignored.
            try:
                _msg = await websocket.receive_text()
            except Exception:
                # Client disconnected or sent malformed data.
                break
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect_customer(session_id, websocket)
