"""The admin transcript must show exactly what the customer received.

Production gap: the repeat circuit breaker appends "If I'm repeating
myself, sorry — reply HUMAN..." AFTER the engine saves its reply, and
nothing wrote the mutated text back. Admins saw the pre-nudge reply;
customers saw the nudge. Every investigation started from a transcript
that did not match reality.
"""
import uuid

import pytest

from app.crud import chat as crud_chat


async def _seed_session(db_session, **overrides):
    from app.models.session import ChatSession

    data = dict(
        org_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        channel="whatsapp",
        channel_user_id="919000000001",
        state_data={},
        missing_fields=[],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    data.update(overrides)
    row = ChatSession(**data)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_update_latest_agent_message_rewrites_body(db_session):
    sess = await _seed_session(db_session)
    await crud_chat.save_message(
        db_session, str(sess.id), "user", "hello?"
    )
    await crud_chat.save_message(
        db_session, str(sess.id), "agent", "original reply"
    )
    changed = await crud_chat.update_latest_agent_message(
        db_session, str(sess.id), "original reply + nudge"
    )
    assert changed is True
    from sqlalchemy.future import select
    from app.models.message import ChatMessage

    rows = (
        await db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sess.id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    assert [r.content for r in rows] == ["hello?", "original reply + nudge"]


@pytest.mark.asyncio
async def test_update_latest_agent_message_skips_user_rows(db_session):
    """The user row must never be touched, even when it is newer."""
    sess = await _seed_session(db_session)
    await crud_chat.save_message(
        db_session, str(sess.id), "agent", "old answer"
    )
    await crud_chat.save_message(
        db_session, str(sess.id), "user", "newer user text"
    )
    changed = await crud_chat.update_latest_agent_message(
        db_session, str(sess.id), "old answer + nudge"
    )
    assert changed is True
    from sqlalchemy.future import select
    from app.models.message import ChatMessage

    rows = (
        await db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sess.id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    assert [r.content for r in rows] == ["old answer + nudge", "newer user text"]


@pytest.mark.asyncio
async def test_update_latest_agent_message_no_agent_row(db_session):
    sess = await _seed_session(db_session)
    changed = await crud_chat.update_latest_agent_message(
        db_session, str(sess.id), "anything"
    )
    assert changed is False


@pytest.mark.asyncio
async def test_breaker_output_matches_saved_transcript(db_session):
    """End-to-end shape of the fix: breaker mutates, transcript follows."""
    from app.core import whatsapp_client
    from types import SimpleNamespace

    sess = await _seed_session(
        db_session,
        state_data={
            "_last_reply_body": "same reply",
            "_repeat_count": 0,
        },
    )
    await crud_chat.save_message(db_session, str(sess.id), "agent", "same reply")
    out = await whatsapp_client.break_identical_repeat(
        db_session, SimpleNamespace(
            session_id=sess.id, collected_data=dict(sess.state_data or {})
        ), "same reply",
    )
    assert out != "same reply"
    assert "HUMAN" in out
    changed = await crud_chat.update_latest_agent_message(
        db_session, str(sess.id), out[:4096]
    )
    assert changed is True
    from sqlalchemy.future import select
    from app.models.message import ChatMessage

    row = (
        await db_session.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == sess.id,
                ChatMessage.role == "agent",
            )
        )
    ).scalars().first()
    assert row.content == out[:4096]
