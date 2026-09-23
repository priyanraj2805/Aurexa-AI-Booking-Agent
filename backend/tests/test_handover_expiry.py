"""Handover auto-expiry: abandoned takeovers return control to the AI."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import chat as crud_chat
from app.models.session import ChatSession
from app.models.message import ChatMessage


def _dt(minutes_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


async def _session(
    db_session: AsyncSession,
    *,
    human_active: bool = True,
    paused_minutes_ago: float = 5,
) -> ChatSession:
    row = ChatSession(
        org_id=uuid.uuid4(),
        channel="web",
        state_data={},
        missing_fields=[],
        current_intent="START_BOOKING",
        session_status="human_active" if human_active else "active",
        human_agent_id=uuid.uuid4() if human_active else None,
        human_agent_active=human_active,
        ai_paused_at=_dt(paused_minutes_ago) if human_active else None,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def _human_msg(db_session: AsyncSession, session_id, minutes_ago: float):
    msg = ChatMessage(
        session_id=session_id,
        role="agent",
        content="hello from support",
        sender_type="human",
        created_at=_dt(minutes_ago),
    )
    db_session.add(msg)
    await db_session.commit()
    return msg


async def _flag(db_session: AsyncSession, session_id) -> bool:
    return await crud_chat.is_human_takeover_active(db_session, str(session_id))


async def test_fresh_takeover_does_not_expire(db_session: AsyncSession):
    row = await _session(db_session, paused_minutes_ago=5)
    assert await crud_chat.maybe_expire_handover(db_session, str(row.id)) is False
    assert await _flag(db_session, row.id) is True


async def test_abandoned_takeover_expires_with_notice(db_session: AsyncSession):
    row = await _session(db_session, paused_minutes_ago=45)
    assert await crud_chat.maybe_expire_handover(db_session, str(row.id)) is True
    assert await _flag(db_session, row.id) is False
    msgs = await crud_chat.get_session_messages(db_session, str(row.id))
    assert any(
        "expired" in (m.content or "").lower() and m.sender_type == "system"
        for m in msgs
    )


async def test_recent_admin_message_extends_takeover(db_session: AsyncSession):
    row = await _session(db_session, paused_minutes_ago=60)
    await _human_msg(db_session, row.id, minutes_ago=5)
    assert await crud_chat.maybe_expire_handover(db_session, str(row.id)) is False
    assert await _flag(db_session, row.id) is True


async def test_stale_admin_message_expires(db_session: AsyncSession):
    row = await _session(db_session, paused_minutes_ago=90)
    await _human_msg(db_session, row.id, minutes_ago=40)
    assert await crud_chat.maybe_expire_handover(db_session, str(row.id)) is True
    assert await _flag(db_session, row.id) is False


async def test_no_takeover_nothing_happens(db_session: AsyncSession):
    row = await _session(db_session, human_active=False)
    assert await crud_chat.maybe_expire_handover(db_session, str(row.id)) is False
    assert await _flag(db_session, row.id) is False


async def test_manual_release_message_unchanged(db_session: AsyncSession):
    row = await _session(db_session, paused_minutes_ago=5)
    await crud_chat.release_human_control(
        db_session, str(row.id), str(row.org_id)
    )
    msgs = await crud_chat.get_session_messages(db_session, str(row.id))
    assert any("left the chat" in (m.content or "") for m in msgs)
    assert not any("expired" in (m.content or "").lower() for m in msgs)
