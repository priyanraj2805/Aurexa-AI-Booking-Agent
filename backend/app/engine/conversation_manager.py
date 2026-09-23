"""
Conversation Manager — central authority for routing decisions.

Responsibilities:
- Determine conversation ownership (AI vs Human)
- Short-circuit AI when a human agent is active
- Provide a single place to extend with future delivery hooks (WebSocket)

This module is the source of truth for "who owns this conversation right now."
All routing decisions for web/widget messages should consult this manager first.

Backward compatible:
- When no human is active, falls through to the existing LangGraph path unchanged.
- Existing callers (runtime_router, message_processor) keep the same signatures.
"""

import logging
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import chat as crud_chat
from app.engine.runtime_router import (
    process_message_with_graph,
    process_message,
)
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.schemas.agent import ActionPayload, ChatSessionState

logger = logging.getLogger(__name__)


class ConversationManager:
    """
    Decides who owns a conversation and dispatches accordingly.

    Usage from runtime_router.route_web_message:
        manager = ConversationManager(db, org, org_config, chat_session)
        return await manager.dispatch(message_text, background_tasks)
    """

    def __init__(
        self,
        db: AsyncSession,
        org: Organization,
        org_config: OrgConfig,
        chat_session: ChatSessionState,
    ):
        self.db = db
        self.org = org
        self.org_config = org_config
        self.chat_session = chat_session
        # Cache for the active handoff record (admin_id, admin_name) populated
        # on first ownership query so we don't hit the DB twice per turn.
        self._admin_cache: Optional[Tuple[str, str]] = None

    # ── Ownership queries ──────────────────────────────────────────────────

    async def is_human_active(self) -> bool:
        """
        Return True if a human agent currently owns the conversation.

        We refresh the flag from the ORM row so any concurrent takeover or
        release (admin clicks while a customer is mid-typing) is respected on
        the very next turn. The chat_session schema field is intentionally
        avoided — Conversation Manager is the single source of truth.
        """
        flag = await self._read_human_active_flag()
        return bool(flag)

    async def get_active_admin(self) -> Optional[Tuple[str, str]]:
        """Return (admin_id, admin_name) of the agent currently controlling the conversation."""
        if self._admin_cache is not None:
            return self._admin_cache
        session_id = self.session_id_str()
        if not session_id:
            return None
        try:
            from sqlalchemy import select
            from app.models.session import ChatSession
            from app.models.user import User

            sess_result = await self.db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            sess = sess_result.scalars().first()
            if not sess or not sess.human_agent_id or not sess.human_agent_active:
                return None
            user_result = await self.db.execute(
                select(User).where(User.id == sess.human_agent_id)
            )
            user = user_result.scalars().first()
            if not user:
                return None
            self._admin_cache = (str(user.id), user.full_name or "Support agent")
            return self._admin_cache
        except Exception:
            return None

    async def _read_human_active_flag(self) -> bool:
        session_id = self.session_id_str()
        if not session_id:
            return False
        try:
            # Abandoned takeovers auto-expire first — a stale takeover can
            # never silence the customer forever.
            await crud_chat.maybe_expire_handover(self.db, session_id)
            from sqlalchemy import select
            from app.models.session import ChatSession

            result = await self.db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            sess = result.scalars().first()
            if not sess:
                return False
            return bool(getattr(sess, "human_agent_active", False))
        except Exception:
            return False

    def session_id_str(self) -> str:
        return str(getattr(self.chat_session, "session_id", "") or "")

    # ── Dispatch ───────────────────────────────────────────────────────────

    async def dispatch(
        self,
        message_text: str,
        background_tasks=None,
        recovery_metadata: Optional[dict] = None,
    ) -> ActionPayload:
        """
        Main entry point. Decides AI vs Human routing.

        Behavior contract:
        - human_agent_active == False  → existing AI path (unchanged)
        - human_agent_active == True   → persist customer message, return
                                        HUMAN_ACTIVE directive, no LLM call
        """
        if await self.is_human_active():
            return await self._handle_human_active(message_text)
        return await self._handle_ai(message_text, background_tasks, recovery_metadata)

    # ── AI path (unchanged behavior) ───────────────────────────────────────

    async def _handle_ai(
        self,
        message_text: str,
        background_tasks,
        recovery_metadata: Optional[dict],
    ) -> ActionPayload:
        """Delegate to existing LangGraph path with canary fallback."""
        try:
            return await process_message_with_graph(
                db=self.db,
                org=self.org,
                org_config=self.org_config,
                chat_session=self.chat_session,
                message_text=message_text,
                background_tasks=background_tasks,
                recovery_metadata=recovery_metadata,
            )
        except Exception as error:
            # Preserve existing canary-fallback behavior from runtime_router.
            from app.engine.runtime_router import (
                is_retryable_runtime_failure,
                emit_fallback_event,
                _build_fallback_event,
                FallbackSunsetError,
            )
            from app.core.config import settings

            if not is_retryable_runtime_failure(error):
                raise

            if not settings.RUNTIME_CANARY_FALLBACK_ENABLED:
                raise

            if settings.RUNTIME_FALLBACK_SUNSET_ENFORCED:
                raise FallbackSunsetError(
                    "Automatic fallback is disabled by Phase 2 sunset gate."
                ) from error

            emit_fallback_event(_build_fallback_event("web", self.chat_session, error))

            return await process_message(
                db=self.db,
                org=self.org,
                org_config=self.org_config,
                chat_session=self.chat_session,
                message_text=message_text,
                background_tasks=background_tasks,
            )

    # ── Human-active path (NEW, non-breaking) ───────────────────────────────

    async def _handle_human_active(self, message_text: str) -> ActionPayload:
        """
        Persist the customer message and signal HUMAN_ACTIVE to the widget.

        The customer message is saved as role='user', sender_type='user'.
        No LLM invocation occurs. The widget renders its existing HUMAN_ACTIVE
        directive handler (it already exists in frontend/chat-widget/src/index.js
        at the directive === "HUMAN_ACTIVE" branch).
        """
        session_id = self.session_id_str()
        try:
            await crud_chat.save_message(
                db=self.db,
                session_id=session_id,
                role="user",
                content=message_text,
                sender_type="user",
            )
            await self.db.commit()
        except Exception as exc:
            logger.warning(
                "conversation_manager.persist_customer_message_failed",
                extra={
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )

        # Notify any subscribed delivery layer (Phase 3 WebSocket broadcast).
        # The hook is best-effort and never raises — booking flow is unaffected.
        try:
            from app.core.ws_manager import ws_manager
            await ws_manager.broadcast_to_session(
                session_id,
                {
                    "type": "customer_message",
                    "session_id": session_id,
                    "content": message_text,
                },
            )
        except Exception as exc:
            logger.debug(
                "conversation_manager.broadcast_skipped",
                extra={"error": str(exc)},
            )

        # Build HUMAN_ACTIVE action. field_meta is populated so the inbox
        # dashboard can show this in its transcript view too.
        admin = await self.get_active_admin()
        admin_name = admin[1] if admin else "Support agent"
        return ActionPayload(
            message="",
            widget_directive="HUMAN_ACTIVE",
            collected_fields=None,
            field_meta={
                "admin_name": admin_name,
                "handoff_active": True,
            },
        )
