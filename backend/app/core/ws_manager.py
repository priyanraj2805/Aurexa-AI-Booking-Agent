"""WebSocket connection manager for admin human handoff and customer widgets."""

from typing import Optional, Set
from fastapi import WebSocket


class WSConnectionManager:
    """Manages WebSocket connections for admin human handoff.

    Customer widget connections are keyed by conversation_id (session_id).
    Admin connections remain keyed by admin_id. The two pools are independent
    so existing admin WS logic is untouched.

    In production, use Redis pub/sub for multi-instance deployments.
    """

    def __init__(self):
        # admin_id -> set of WebSocket connections (UNCHANGED)
        self.admin_connections: dict[str, Set[WebSocket]] = {}
        # conversation_id -> admin_id (who is currently controlling) (UNCHANGED)
        self.conversation_control: dict[str, str] = {}

        # ── Customer widget connections (NEW, additive) ──────────────────
        # session_id -> set of WebSocket connections (one customer can have
        # multiple browser tabs open to the same widget session).
        self.customer_connections: dict[str, Set[WebSocket]] = {}

    # ── Admin API (UNCHANGED) ────────────────────────────────────────────

    async def connect_admin(self, admin_id: str, websocket: WebSocket):
        await websocket.accept()
        if admin_id not in self.admin_connections:
            self.admin_connections[admin_id] = set()
        self.admin_connections[admin_id].add(websocket)

    def disconnect_admin(self, admin_id: str, websocket: WebSocket):
        if admin_id in self.admin_connections:
            self.admin_connections[admin_id].discard(websocket)
            if not self.admin_connections[admin_id]:
                del self.admin_connections[admin_id]

    async def send_to_admin(self, admin_id: str, message: dict):
        """Send message to all connections of an admin."""
        if admin_id in self.admin_connections:
            disconnected = set()
            for ws in self.admin_connections[admin_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    disconnected.add(ws)
            for ws in disconnected:
                self.disconnect_admin(admin_id, ws)

    def get_controlling_admin(self, conversation_id: str) -> Optional[str]:
        return self.conversation_control.get(conversation_id)

    def set_conversation_control(self, conversation_id: str, admin_id: str):
        self.conversation_control[conversation_id] = admin_id

    def release_conversation_control(self, conversation_id: str):
        self.conversation_control.pop(conversation_id, None)

    # ── Customer widget API (NEW) ────────────────────────────────────────

    async def connect_customer(self, session_id: str, websocket: WebSocket):
        """Register a customer widget connection keyed by session_id."""
        await websocket.accept()
        if session_id not in self.customer_connections:
            self.customer_connections[session_id] = set()
        self.customer_connections[session_id].add(websocket)

    def disconnect_customer(self, session_id: str, websocket: WebSocket):
        """Unregister a customer widget connection. Safe if already removed."""
        conns = self.customer_connections.get(session_id)
        if conns is None:
            return
        conns.discard(websocket)
        if not conns:
            self.customer_connections.pop(session_id, None)

    async def broadcast_to_session(self, session_id: str, event: dict) -> int:
        """Push an event to all customer widget connections for this session.

        Returns the number of sockets that successfully received the event.
        Failed sends are silently dropped so the booking flow is never blocked.
        """
        conns = self.customer_connections.get(session_id)
        if not conns:
            return 0
        delivered = 0
        dead: Set[WebSocket] = set()
        for ws in conns:
            try:
                await ws.send_json(event)
                delivered += 1
            except Exception:
                dead.add(ws)
        for ws in dead:
            conns.discard(ws)
        if not conns:
            self.customer_connections.pop(session_id, None)
        return delivered

    def has_customer_connection(self, session_id: str) -> bool:
        return bool(self.customer_connections.get(session_id))


# Global instance
ws_manager = WSConnectionManager()