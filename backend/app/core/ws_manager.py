"""WebSocket connection manager for admin human handoff."""

from typing import Optional, Set
from fastapi import WebSocket


class WSConnectionManager:
    """Manages WebSocket connections for admin human handoff.
    
    In production, use Redis pub/sub for multi-instance deployments.
    """
    
    def __init__(self):
        # admin_id -> set of WebSocket connections
        self.admin_connections: dict[str, Set[WebSocket]] = {}
        # conversation_id -> admin_id (who is currently controlling)
        self.conversation_control: dict[str, str] = {}

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


# Global instance
ws_manager = WSConnectionManager()