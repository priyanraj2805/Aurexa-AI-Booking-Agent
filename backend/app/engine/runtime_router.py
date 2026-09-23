import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import chat as crud_chat
from app.engine.session_recovery import SessionRecoveryManager
from app.engine import langgraph_orchestrator
from app.engine import message_processor
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.schemas.agent import ActionPayload, ChatSessionState

logger = logging.getLogger(__name__)


class FallbackSunsetError(RuntimeError):
    """Raised when automatic fallback is blocked by the phase-end sunset gate."""


async def recover_session_state(
    db: AsyncSession,
    chat_session: ChatSessionState,
    *,
    channel: str,
    org_id: Optional[str] = None,
    channel_user_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    manager = SessionRecoveryManager(
        get_checkpoint_by_session=lambda session_id: crud_chat.get_latest_session_checkpoint(
            db, session_id
        ),
        get_checkpoint_by_channel=lambda _org_id, _channel, _channel_user_id: crud_chat.get_latest_channel_checkpoint(
            db, _org_id, _channel, _channel_user_id
        ),
    )
    recovery = await manager.recover(
        channel=channel,
        session_id=getattr(chat_session, "session_id", None),
        org_id=org_id,
        channel_user_id=channel_user_id or getattr(chat_session, "channel_user_id", None),
        memory_state={
            "collected_data": dict(getattr(chat_session, "collected_data", {}) or {}),
            "memory_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if not recovery:
        return None

    state = recovery.state
    if isinstance(state.get("collected_data"), dict):
        chat_session.collected_data = state["collected_data"]
    if isinstance(state.get("missing_fields"), list):
        chat_session.missing_fields = state["missing_fields"]
    if state.get("current_intent") is not None:
        chat_session.current_intent = state["current_intent"]
    # NOTE: session_status is intentionally NOT restored from the checkpoint.
    # Checkpoints are written mid-turn (before booking/OTP completes), so their
    # session_status is always stale relative to the chat_sessions row, which is
    # the authoritative lifecycle status (ACTIVE/CONFIRMING/CONFIRMED/CANCELLED/
    # EXPIRED). Restoring it here would clobber a CONFIRMED/CANCELLED session
    # back to ACTIVE and break the post-booking gate on the next message.

    return {
        "policy": recovery.policy,
        "prompt_metadata": recovery.prompt_metadata,
        "merge_metadata": recovery.merge_metadata,
        "checkpoint_ts": recovery.checkpoint_ts,
    }


async def process_message_with_graph(**kwargs) -> ActionPayload:
    return await langgraph_orchestrator.process_message_with_graph(**kwargs)


async def process_message(**kwargs) -> ActionPayload:
    return await message_processor.process_message(**kwargs)


def is_retryable_runtime_failure(error: Exception) -> bool:
    retryable_types = (
        TimeoutError,
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
        RuntimeError,
        httpx.TimeoutException,
        httpx.TransportError,
    )
    return isinstance(error, retryable_types)


def emit_fallback_event(event: dict[str, Any]) -> None:
    logger.warning("runtime_router_fallback_activated", extra={"event": event})


def _build_fallback_event(
    channel: str,
    chat_session: ChatSessionState,
    error: Exception,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "session_id": getattr(chat_session, "session_id", ""),
        "fallback_to": "legacy",
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


async def route_web_message(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    recovery_meta: Optional[dict[str, Any]] = None
    try:
        recovery_meta = await recover_session_state(
            db,
            chat_session,
            channel="web",
            org_id=str(org.id),
        )
    except Exception as recovery_error:
        logger.warning(
            "runtime_router_recovery_failed",
            extra={"error_type": type(recovery_error).__name__, "error_message": str(recovery_error)},
        )

    # Delegate routing decision to ConversationManager. When no human is
    # active, the manager falls through to the existing LangGraph path with
    # the same canary-fallback behavior as before. When a human is active,
    # the manager short-circuits the AI path (Phase 1+).
    from app.engine.conversation_manager import ConversationManager

    manager = ConversationManager(
        db=db,
        org=org,
        org_config=org_config,
        chat_session=chat_session,
    )
    return await manager.dispatch(
        message_text=message_text,
        background_tasks=background_tasks,
        recovery_metadata=recovery_meta,
    )


async def route_whatsapp_message(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """WhatsApp uses the same LangGraph runtime as the web widget, so both
    channels share booking flow, RAG grounding, validators, and OTP handling.

    The legacy pipeline remains as a loud fallback: WhatsApp has no error UI,
    so a graph failure must never silently kill customer replies. Fallback
    activations are logged with full tracebacks for ops visibility.
    """
    # Human handover: when an admin owns the conversation the AI must stay
    # silent on WhatsApp exactly like it does on web (ConversationManager).
    # The flag is re-read from the DB so a mid-typing takeover applies now.
    try:
        if await crud_chat.is_human_takeover_active(db, str(getattr(chat_session, "session_id", "") or "")):
            try:
                await crud_chat.save_message(db, str(chat_session.session_id), "user", message_text)
                await db.commit()
            except Exception:
                pass
            from app.schemas.agent import ActionPayload as _AP

            return _AP(message="", widget_directive="HUMAN_ACTIVE", field_meta={"handoff_active": True})
    except Exception:
        pass
    try:
        return await process_message_with_graph(
            db=db,
            org=org,
            org_config=org_config,
            chat_session=chat_session,
            message_text=message_text,
            background_tasks=background_tasks,
        )
    except Exception as error:
        logger.exception("route_whatsapp_message graph failed; falling back to legacy")
        emit_fallback_event(_build_fallback_event("whatsapp", chat_session, error))
        return await process_message(
            db=db,
            org=org,
            org_config=org_config,
            chat_session=chat_session,
            message_text=message_text,
            background_tasks=background_tasks,
        )
