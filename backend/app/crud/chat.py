import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified

WHATSAPP_SESSION_TTL = timedelta(hours=8)
# Channels that use the 8-hour inactivity expiry (WhatsApp). Web keeps its own lifecycle.
WHATSAPP_EXPIRY_CHANNELS = {"whatsapp", "whatsapp_cloud"}

from app.models.session import ChatSession
from app.models.config import OrgConfig
from app.models.message import ChatMessage
from app.schemas.chat import ChatSessionCreate
from app.core.telemetry import increment_usage
from app.schemas.agent import (
    ChatSessionState,
    FieldDefinition,
    parse_field_definitions,
    get_required_field_names,
    order_field_definitions,
)

RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
FIELD_DEFINITIONS_SNAPSHOT_KEY = "field_definitions_snapshot"


async def create_chat_session(
    db: AsyncSession, org_id: str, channel: str = "web", channel_user_id: str = None, mode: Optional[str] = None
) -> ChatSession:
    # Get org config and derive field names from definitions
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    org_config = result.scalars().first()

    if not org_config:
        raise ValueError("Organization configuration not found")

    field_defs = order_field_definitions(
        parse_field_definitions(
            org_config.field_definitions or org_config.required_fields
        )
    )
    required_names = get_required_field_names(field_defs)
    snapshot = [field_def.model_dump(mode="python") for field_def in field_defs]

    now = datetime.now(timezone.utc)
    # Ask-AI sessions start with zero collection state: no missing fields,
    # no booking intent. The guided-booking flow recomputes its missing
    # list from field definitions on its first collection turn, so this
    # seed only changes how ask sessions begin.
    _ask_seed = str(mode or "").strip().lower() == "ask"
    db_session = ChatSession(
        org_id=uuid.UUID(org_id),
        channel=channel,
        channel_user_id=channel_user_id,
        state_data={FIELD_DEFINITIONS_SNAPSHOT_KEY: snapshot},
        missing_fields=[] if _ask_seed else required_names,
        current_intent="ASK_QUESTION" if _ask_seed else "START_BOOKING",
        last_activity_at=now,
    )
    db.add(db_session)
    await db.commit()
    await db.refresh(db_session)
    await increment_usage(uuid.UUID(org_id), "conversations")
    return db_session


async def get_chat_session(
    db: AsyncSession, session_id: str
) -> Optional[ChatSessionState]:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    db_session = result.scalars().first()

    if not db_session:
        return None

    return ChatSessionState(
        session_id=str(db_session.id),
        org_id=str(db_session.org_id),
        channel=db_session.channel or "web",
        channel_user_id=db_session.channel_user_id,
        collected_data=db_session.state_data,
        missing_fields=db_session.missing_fields,
        current_intent=db_session.current_intent,
        session_status=db_session.session_status or "ACTIVE",
        otp_verified=db_session.otp_verified or False,
        phone_otp_verified=getattr(db_session, "phone_otp_verified", False) or False,
        pending_cancel=db_session.pending_cancel or False,
    )


def _is_session_expired(last_activity: Optional[datetime], now: datetime) -> bool:
    """True if now - last_activity > 8h. last_activity is made tz-aware."""
    if last_activity is None:
        return False
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last_activity) > WHATSAPP_SESSION_TTL


async def get_or_create_channel_session(
    db: AsyncSession, org_id: str, channel: str, channel_user_id: str
) -> ChatSessionState:
    """Find an active session for a channel user, or create a new one.

    WhatsApp inactivity expiry (8h) is enforced BEFORE returning a session,
    and BEFORE any LLM history is loaded — old context is never sent to the
    agent after expiry. See pseudo-code in the task description.
    """
    now = datetime.now(timezone.utc)
    # Per-user lock to prevent two simultaneous webhooks after expiry from
    # creating two active sessions. Uses the in-memory debouncer lock keyed by
    # channel+user (not session_id, which doesn't exist yet).
    _lock_key = f"get_or_create:{org_id}:{channel}:{channel_user_id}"
    try:
        from app.core.whatsapp_debouncer import get_session_lock as _get_lock

        _lock = _get_lock(_lock_key)
    except Exception:
        _lock = None

    async def _run() -> ChatSessionState:
        # Use FOR UPDATE to lock the active row while we decide to expire it.
        result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.org_id == org_id,
                ChatSession.channel == channel,
                ChatSession.channel_user_id == channel_user_id,
                ChatSession.session_status.in_(
                    ["ACTIVE", "CONFIRMING", "human_active", "active"]
                ),
            )
            .order_by(ChatSession.updated_at.desc())
            .with_for_update()
        )
        db_session = result.scalars().first()

        if db_session is not None:
            # 8-hour inactivity check — only for WhatsApp channels
            if channel in WHATSAPP_EXPIRY_CHANNELS:
                last_activity = getattr(db_session, "last_activity_at", None)
                if last_activity is None:
                    last_activity = getattr(db_session, "updated_at", None) or getattr(
                        db_session, "created_at", None
                    )
                if _is_session_expired(last_activity, now):
                    # Expire old session — keep row for audit, never reuse
                    db_session.session_status = "EXPIRED"
                    db_session.is_active = False
                    # Expire any pending BookingDirectives for the old session
                    # so they are never considered active in the new conversation.
                    try:
                        from sqlalchemy import update as _update
                        from app.models.booking_directive import BookingDirective

                        await db.execute(
                            _update(BookingDirective)
                            .where(
                                BookingDirective.session_id == db_session.id,
                                BookingDirective.status == "PENDING",
                            )
                            .values(status="EXPIRED")
                        )
                    except Exception:
                        pass
                    # Do not bump last_activity_at on expired row
                    try:
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise
                    # Create fresh session — reset all temporary state
                    new_session = await create_chat_session(
                        db, org_id, channel=channel, channel_user_id=channel_user_id
                    )
                    return await get_chat_session(db, str(new_session.id))
            # Continue existing session — bump last_activity_at
            try:
                db_session.last_activity_at = now
                await db.commit()
                await db.refresh(db_session)
            except Exception:
                await db.rollback()
                # Non-fatal: still return session
                pass
            return ChatSessionState(
                session_id=str(db_session.id),
                org_id=str(db_session.org_id),
                channel=db_session.channel or channel,
                channel_user_id=db_session.channel_user_id,
                collected_data=db_session.state_data,
                missing_fields=db_session.missing_fields,
                current_intent=db_session.current_intent,
                session_status=db_session.session_status or "ACTIVE",
                otp_verified=db_session.otp_verified or False,
                phone_otp_verified=getattr(db_session, "phone_otp_verified", False) or False,
                pending_cancel=db_session.pending_cancel or False,
            )

        # No active session — create new
        new_session = await create_chat_session(
            db, org_id, channel=channel, channel_user_id=channel_user_id
        )
        return await get_chat_session(db, str(new_session.id))

    if _lock is not None:
        async with _lock:
            return await _run()
    return await _run()


async def update_chat_session(
    db: AsyncSession,
    session_id: str,
    state: ChatSessionState,
    *,
    remove_keys: Tuple[str, ...] = (),
) -> ChatSession:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    db_session = result.scalars().first()

    if db_session:
        existing_state = (
            dict(db_session.state_data) if isinstance(db_session.state_data, dict) else {}
        )
        incoming_state = (
            dict(state.collected_data) if isinstance(state.collected_data, dict) else {}
        )
        if (
            FIELD_DEFINITIONS_SNAPSHOT_KEY in existing_state
            and FIELD_DEFINITIONS_SNAPSHOT_KEY not in incoming_state
        ):
            incoming_state[FIELD_DEFINITIONS_SNAPSHOT_KEY] = existing_state[
                FIELD_DEFINITIONS_SNAPSHOT_KEY
            ]
        existing_state.update(incoming_state)
        for key in remove_keys:
            existing_state.pop(str(key), None)
        db_session.state_data = existing_state
        db_session.missing_fields = state.missing_fields
        db_session.current_intent = state.current_intent
        db_session.session_status = state.session_status
        db_session.otp_verified = state.otp_verified
        db_session.phone_otp_verified = getattr(state, "phone_otp_verified", False)
        db_session.pending_cancel = state.pending_cancel
        flag_modified(db_session, "state_data")
        flag_modified(db_session, "missing_fields")
        await db.commit()
        await db.refresh(db_session)

    return db_session


def _extract_checkpoint(state_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(state_data, dict):
        return None
    checkpoint = state_data.get(RUNTIME_CHECKPOINT_KEY)
    if isinstance(checkpoint, dict):
        return checkpoint
    return None


async def save_session_checkpoint(
    db: AsyncSession,
    session_id: str,
    checkpoint: Dict[str, Any],
) -> Optional[ChatSession]:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    db_session = result.scalars().first()

    if not db_session:
        return None

    state_data = db_session.state_data if isinstance(db_session.state_data, dict) else {}
    state_data[RUNTIME_CHECKPOINT_KEY] = checkpoint

    checkpoint_state = checkpoint.get("state", {})
    checkpoint_collected = checkpoint_state.get("collected_data", {})
    if isinstance(checkpoint_collected, dict):
        state_data.update(checkpoint_collected)

    db_session.state_data = state_data
    flag_modified(db_session, "state_data")
    await db.commit()
    await db.refresh(db_session)
    return db_session


async def get_latest_session_checkpoint(
    db: AsyncSession,
    session_id: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    db_session = result.scalars().first()
    if not db_session:
        return None

    checkpoint = _extract_checkpoint(db_session.state_data)
    if checkpoint is None:
        return None
    return str(db_session.id), checkpoint


async def get_latest_channel_checkpoint(
    db: AsyncSession,
    org_id: str,
    channel: str,
    channel_user_id: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    # Only return checkpoint from an ACTIVE conversation — expired sessions
    # (8h inactivity) keep their rows for audit but are never used for LLM
    # context after expiry. This prevents old booking/OTP state from leaking
    # into a new session.
    result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.org_id == org_id,
            ChatSession.channel == channel,
            ChatSession.channel_user_id == channel_user_id,
            ChatSession.is_active.is_(True),
            ChatSession.session_status.in_(["ACTIVE", "CONFIRMING"]),
        )
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = result.scalars().all()
    now = datetime.now(timezone.utc)
    for session in sessions:
        # Enforce 8h window at read time as well (defense-in-depth). If the
        # row somehow survived as ACTIVE past 8h, treat it as expired for
        # checkpoint purposes.
        if channel in WHATSAPP_EXPIRY_CHANNELS:
            last_activity = getattr(session, "last_activity_at", None) or getattr(
                session, "updated_at", None
            )
            if last_activity is not None and _is_session_expired(last_activity, now):
                continue
        checkpoint = _extract_checkpoint(session.state_data)
        if checkpoint is not None:
            return str(session.id), checkpoint
    return None


async def atomic_set_confirming(db: AsyncSession, session_id: str) -> bool:
    """
    Atomically transitions a session from ACTIVE → CONFIRMING using SELECT FOR UPDATE.
    Returns True if the transition succeeded, False if the session was already
    in a non-ACTIVE state (another request got there first).
    """
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .with_for_update(nowait=True)
    )
    db_session = result.scalars().first()

    if not db_session:
        return False

    # Recovery path: if OTP is already verified but status got stuck in CONFIRMING,
    # allow re-entry so booking finalize can continue deterministically.
    if db_session.session_status == "CONFIRMING" and db_session.otp_verified:
        db_session.session_status = "ACTIVE"
        await db.commit()

    if db_session.session_status != "ACTIVE":
        return False

    db_session.session_status = "CONFIRMING"
    await db.commit()
    return True


async def get_org_required_fields(db: AsyncSession, org_id: str) -> list:
    """
    Fetches the tenant's configured required field names from OrgConfig.
    Falls back to the standard set if no config is found.
    """
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    org_config = result.scalars().first()
    if org_config:
        field_defs = parse_field_definitions(
            org_config.field_definitions or org_config.required_fields
        )
        return get_required_field_names(field_defs)
    return ["name", "email", "phone", "date", "time"]


async def get_org_field_definitions(
    db: AsyncSession, org_id: str
) -> List[FieldDefinition]:
    """
    Fetches the tenant's full field definitions from OrgConfig.
    Returns parsed FieldDefinition list (handles legacy flat arrays).
    """
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    org_config = result.scalars().first()
    if org_config:
        return order_field_definitions(
            parse_field_definitions(
                org_config.field_definitions or org_config.required_fields
            )
        )
    return order_field_definitions(parse_field_definitions(None))


# ── Message transcript helpers ────────────────────────────────────────


async def save_message(
    db: AsyncSession,
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[dict] = None,
    sender_type: Optional[str] = None,
    human_agent_id: Optional[str] = None,
    channel: Optional[str] = None,
    direction: Optional[str] = None,
    send_status: Optional[str] = None,
    provider_message_id: Optional[str] = None,
    external_message_id: Optional[str] = None,
) -> ChatMessage:
    """Persist a single chat message (user or agent).

    Outbound tracking: every assistant message is GENERATED at creation,
    then SENT/FAILED after a provider attempt, and DELIVERED/READ after
    a provider callback. Transcript existence is not delivery proof.
    """
    import logging as _lg

    _logger = _lg.getLogger(__name__)
    effective_sender_type = sender_type or ("user" if role == "user" else "ai")
    # Infer channel/direction/send_status if not provided
    if channel is None:
        try:
            _sess = (await db.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(session_id)))).scalars().first()
            if _sess is not None:
                channel = getattr(_sess, "channel", None)
        except Exception:
            pass
    if direction is None:
        direction = "inbound" if role == "user" else "outbound"
    if send_status is None and role == "agent":
        # User messages have no send_status; agent outbound starts GENERATED
        send_status = "GENERATED" if effective_sender_type in ("ai", "human") else None
        # Human handoff messages are considered SENT immediately (no provider)
        if effective_sender_type == "human":
            send_status = "SENT"
    msg = ChatMessage(
        session_id=uuid.UUID(session_id),
        role=role,
        content=content,
        metadata_=metadata,
        sender_type=effective_sender_type,
        human_agent_id=uuid.UUID(human_agent_id) if human_agent_id else None,
    )
    # Set outbound tracking fields if they exist on the model
    #
    # external_message_id means "the provider message that PRODUCED this row".
    # For a user turn that is the inbound id (wamid / Baileys key.id), which
    # the inbound handler scopes ambiently so no call site can forget it —
    # helper flows (cancel lookup, check-booking, decline/refusal) were
    # silently writing NULL before.
    #
    # The ambient fallback applies to USER ROWS ONLY. Applying it to every row
    # stamped the bot's own reply with the inbound message's id, so a user row
    # and the agent row that answered it shared one id and the column could not
    # be used to trace a conversation. Agent rows carry their own outbound id in
    # provider_message_id, and an explicit argument always wins.
    _role_key = str(role or "").strip().lower()
    if external_message_id is None and _role_key == "user":
        try:
            from app.core.inbound_context import get_inbound_message_id

            external_message_id = get_inbound_message_id()
        except Exception:
            external_message_id = None
    for _attr, _val in [
        ("channel", channel),
        ("direction", direction),
        ("send_status", send_status),
        ("provider_message_id", provider_message_id),
        ("external_message_id", external_message_id),
    ]:
        try:
            if hasattr(msg, _attr) and _val is not None:
                setattr(msg, _attr, _val)
        except Exception:
            pass
    db.add(msg)
    # Bump the parent session so the admin inbox (sorted by updated_at)
    # reorders live when polling — a message insert alone never touches it.
    # For the 8-hour inactivity window, only USER messages count as activity
    # (bot replies must not extend the window).
    try:
        from datetime import datetime, timezone

        parent = (
            await db.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(session_id)))
        ).scalars().first()
        if parent is not None:
            now = datetime.now(timezone.utc)
            parent.updated_at = now
            if role == "user" or effective_sender_type == "user":
                try:
                    parent.last_activity_at = now
                except Exception:
                    pass
    except Exception:
        pass
    await db.commit()
    await db.refresh(msg)
    return msg


async def mark_latest_outbound_sent(
    db: AsyncSession,
    session_id: str,
    provider_message_id: Optional[str] = None,
) -> Optional[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == uuid.UUID(str(session_id)),
            ChatMessage.role == "agent",
            ChatMessage.sender_type != "system",
            ChatMessage.direction == "outbound",
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    message = result.scalars().first()
    if message is None:
        return None
    message.send_status = "SENT"
    message.sent_at = datetime.now(timezone.utc)
    if provider_message_id:
        message.provider_message_id = str(provider_message_id)
    await db.commit()
    await db.refresh(message)
    return message


async def mark_latest_outbound_failed(
    db: AsyncSession,
    session_id: str,
    error_message: str,
) -> Optional[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == uuid.UUID(str(session_id)),
            ChatMessage.role == "agent",
            ChatMessage.sender_type != "system",
            ChatMessage.direction == "outbound",
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    message = result.scalars().first()
    if message is None:
        return None
    message.send_status = "FAILED"
    message.failed_at = datetime.now(timezone.utc)
    message.error_message = str(error_message)[:2000]
    await db.commit()
    await db.refresh(message)
    return message


async def apply_delivery_status(
    db: AsyncSession,
    provider_message_id: str,
    status: str,
    error_message: Optional[str] = None,
) -> Optional[ChatMessage]:
    normalized = str(provider_message_id or "").strip()
    if not normalized:
        return None
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.provider_message_id == normalized
        )
    )
    message = result.scalars().first()
    if message is None:
        return None
    value = str(status or "").upper()
    now = datetime.now(timezone.utc)
    if value in {"SENT", "ACCEPTED"}:
        message.send_status = "SENT"
        message.sent_at = message.sent_at or now
    elif value == "DELIVERED":
        message.send_status = "DELIVERED"
        message.delivered_at = now
        message.sent_at = message.sent_at or now
    elif value == "READ":
        message.send_status = "READ"
        message.read_at = now
        message.delivered_at = message.delivered_at or now
        message.sent_at = message.sent_at or now
    elif value in {"FAILED", "DELETED", "UNREAD"}:
        message.send_status = "FAILED"
        message.failed_at = now
        message.error_message = str(error_message or value)[:2000]
    else:
        return message
    await db.commit()
    await db.refresh(message)
    return message


async def get_session_messages(
    db: AsyncSession,
    session_id: str,
    limit: int = 200,
) -> List[ChatMessage]:
    """Return messages for a session, oldest first."""
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    return result.scalars().all()


# ── Human Handoff helpers ────────────────────────────────────────────


async def is_human_takeover_active(db: AsyncSession, session_id: str) -> bool:
    """Fresh read of the human-handover flag (never from cached state).

    WhatsApp and web inbound paths call this on EVERY message so an admin
    takeover/release mid-typing is respected on the very next turn.

    Abandoned takeovers auto-expire first (see `maybe_expire_handover`), so
    a stale takeover can never silence the customer forever.
    """
    try:
        await maybe_expire_handover(db, session_id)
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        sess = result.scalars().first()
        return bool(sess and getattr(sess, "human_agent_active", False))
    except Exception:
        return False


async def set_human_takeover(
    db: AsyncSession,
    session_id: str,
    org_id: str,
    admin_id: str,
    admin_name: str,
) -> Optional[ChatSession]:
    """Atomically set human takeover for a conversation."""
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id, ChatSession.org_id == org_id)
        .with_for_update(nowait=True)
    )
    session = result.scalars().first()

    if not session:
        return None

    session.human_agent_id = uuid.UUID(admin_id)
    session.human_agent_active = True
    session.ai_paused_at = func.now()
    session.session_status = "human_active"

    # Save system message
    await save_message(
        db, session_id, "agent",
        f"👨‍💼 {admin_name} joined the chat",
        sender_type="system",
        metadata={"human_joined": True, "admin_name": admin_name}
    )

    await db.commit()
    await db.refresh(session)
    return session


async def release_human_control(
    db: AsyncSession,
    session_id: str,
    org_id: str,
    reason: str = "released",
) -> Optional[ChatSession]:
    """Atomically release human control back to AI.

    `reason` is "released" (admin clicked Release) or "expired" (abandoned
    takeover auto-released) — the inbox system notice reflects it.
    """
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id, ChatSession.org_id == org_id)
        .with_for_update(nowait=True)
    )
    session = result.scalars().first()

    if not session:
        return None

    session.human_agent_id = None
    session.human_agent_active = False
    session.ai_paused_at = None
    session.session_status = "active"

    # Save system message
    if reason == "expired":
        notice = (
            "👨‍💼 Handover expired after 30 minutes without an admin reply. "
            "AI assistant resumed."
        )
        meta = {"human_left": True, "auto_expired": True}
    else:
        notice = "👨‍💼 Support agent left the chat. AI assistant resumed."
        meta = {"human_left": True}
    await save_message(
        db, session_id, "agent",
        notice,
        sender_type="system",
        metadata=meta
    )

    await db.commit()
    await db.refresh(session)
    return session


def _handover_timeout_minutes() -> int:
    try:
        from app.core.config import settings as _settings

        return max(0, int(getattr(_settings, "HANDOVER_AUTO_RELEASE_MINUTES", 30) or 0))
    except Exception:
        return 30


async def maybe_expire_handover(db: AsyncSession, session_id: str) -> bool:
    """Auto-release an abandoned takeover. Returns True if released now.

    A takeover expires when no admin (human) message arrived within
    HANDOVER_AUTO_RELEASE_MINUTES of the last admin message — or of the
    takeover itself when the admin never wrote. The inbox gets the same
    system notice as a manual release. Best-effort: any failure keeps the
    current state (never breaks message handling).
    """
    try:
        timeout = _handover_timeout_minutes()
        if timeout <= 0:
            return False
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        session = result.scalars().first()
        if session is None or not getattr(session, "human_agent_active", False):
            return False
        last_human = await db.execute(
            select(ChatMessage.created_at)
            .where(
                ChatMessage.session_id == session.id,
                ChatMessage.sender_type == "human",
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        last_human_at = last_human.scalars().first()
        baseline = last_human_at or getattr(session, "ai_paused_at", None)
        if baseline is None:
            return False
        try:
            from datetime import datetime as _dt, timezone as _tz

            now = _dt.now(_tz.utc)
            if baseline.tzinfo is None:
                baseline = baseline.replace(tzinfo=_tz.utc)
            idle_seconds = (now - baseline).total_seconds()
        except Exception:
            return False
        if idle_seconds < timeout * 60:
            return False
        try:
            await release_human_control(
                db, str(session.id), str(session.org_id), reason="expired"
            )
            return True
        except Exception:
            return False
    except Exception:
        return False


async def save_human_message(
    db: AsyncSession,
    session_id: str,
    admin_id: str,
    content: str,
) -> ChatMessage:
    """Save a message from human agent."""
    msg = ChatMessage(
        session_id=uuid.UUID(session_id),
        role="agent",
        content=content,
        sender_type="human",
        human_agent_id=uuid.UUID(admin_id),
        metadata_={"human_agent": True},
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def update_latest_agent_message(
    db: AsyncSession, session_id: str, content: str
) -> bool:
    """Replace the content of this session's most recent agent message.

    The repeat circuit breaker (and any other transport-level rewrite)
    mutates the reply AFTER the engine already saved it, so without this
    the admin transcript shows a different text than the customer received.
    Returns True when a row was updated. Fail-open: never raises.
    """
    try:
        row = (
            await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == uuid.UUID(str(session_id)),
                    ChatMessage.role == "agent",
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if row is None:
            return False
        row.content = content
        await db.commit()
        return True
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        return False
