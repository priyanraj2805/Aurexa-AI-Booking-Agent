from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.engine.session_recovery import SessionRecoveryManager


def _checkpoint(now: datetime, minutes_old: int = 0) -> dict:
    ts = (now - timedelta(minutes=minutes_old)).isoformat()
    return {
        "checkpoint_ts": ts,
        "state": {
            "collected_data": {"name": "Ada", "email": "ada@checkpoint.test"},
            "missing_fields": [],
            "current_intent": "CONFIRM_BOOKING",
            "session_status": "ACTIVE",
        },
    }


@pytest.mark.asyncio
async def test_recovery_uses_channel_aware_lookup_keys():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    by_session = AsyncMock(return_value=("session-web", _checkpoint(now)))
    by_channel = AsyncMock(return_value=("session-wa", _checkpoint(now)))
    manager = SessionRecoveryManager(
        get_checkpoint_by_session=by_session,
        get_checkpoint_by_channel=by_channel,
        now=lambda: now,
    )

    await manager.recover(session_id="session-web", channel="web")
    await manager.recover(org_id="org-1", channel="whatsapp", channel_user_id="+15550001111")

    by_session.assert_awaited_once_with("session-web")
    by_channel.assert_awaited_once_with("org-1", "whatsapp", "+15550001111")


@pytest.mark.asyncio
async def test_recovery_within_15_minutes_auto_continues_silently():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    by_session = AsyncMock(return_value=("session-web", _checkpoint(now, minutes_old=10)))
    manager = SessionRecoveryManager(
        get_checkpoint_by_session=by_session,
        get_checkpoint_by_channel=AsyncMock(),
        now=lambda: now,
    )

    result = await manager.recover(session_id="session-web", channel="web")

    assert result.policy == "auto_continue"
    assert result.prompt_metadata is None
    assert result.state["current_intent"] == "CONFIRM_BOOKING"


@pytest.mark.asyncio
async def test_recovery_after_15_minutes_returns_resume_or_start_new_metadata():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    by_session = AsyncMock(return_value=("session-web", _checkpoint(now, minutes_old=16)))
    manager = SessionRecoveryManager(
        get_checkpoint_by_session=by_session,
        get_checkpoint_by_channel=AsyncMock(),
        now=lambda: now,
    )

    result = await manager.recover(session_id="session-web", channel="web")

    assert result.policy == "resume_or_start_new"
    assert result.prompt_metadata is not None
    assert result.prompt_metadata["default_action"] == "start_new"
    assert result.prompt_metadata["last_intent"] == "CONFIRM_BOOKING"


@pytest.mark.asyncio
async def test_divergent_memory_prefers_newest_checkpoint_and_surfaces_merge_metadata():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    by_session = AsyncMock(return_value=("session-web", _checkpoint(now, minutes_old=1)))
    manager = SessionRecoveryManager(
        get_checkpoint_by_session=by_session,
        get_checkpoint_by_channel=AsyncMock(),
        now=lambda: now,
    )

    memory_state = {
        "collected_data": {"name": "Ada", "email": "stale@memory.test"},
        "memory_updated_at": (now - timedelta(minutes=5)).isoformat(),
    }
    result = await manager.recover(
        session_id="session-web",
        channel="web",
        memory_state=memory_state,
    )

    assert result.state["collected_data"]["email"] == "ada@checkpoint.test"
    assert result.merge_metadata["winner"] == "checkpoint"
    assert "email" in result.merge_metadata["conflicting_fields"]
