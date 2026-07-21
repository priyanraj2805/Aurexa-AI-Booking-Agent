from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.engine.checkpoint_manager import CheckpointManager


def _base_state() -> dict:
    return {
        "collected_data": {"name": "Ada"},
        "missing_fields": ["email"],
        "current_intent": "COLLECT_FIELDS",
        "session_status": "ACTIVE",
        "summary": "Known details: name=Ada",
        "context_window": [{"role": "human", "content": "My name is Ada"}],
    }


@pytest.mark.asyncio
async def test_high_value_events_are_written_and_duplicate_turn_events_are_coalesced():
    write_checkpoint = AsyncMock()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    manager = CheckpointManager(
        write_checkpoint=write_checkpoint,
        now=lambda: now,
        debounce_seconds=30,
    )

    await manager.record_event("session-1", "field_capture", _base_state())
    await manager.record_event("session-1", "field_capture", _base_state())
    await manager.record_event("session-1", "typing", _base_state())

    assert write_checkpoint.await_count == 1


@pytest.mark.asyncio
async def test_checkpoint_state_is_canonical_and_excludes_memory_only_artifacts():
    write_checkpoint = AsyncMock()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    manager = CheckpointManager(write_checkpoint=write_checkpoint, now=lambda: now)

    state = {
        **_base_state(),
        "messages": [{"role": "user", "content": "hello"}],
        "memory_only_cache": {"llm_tokens": 12},
        "transient_buffer": ["debug-entry"],
    }

    await manager.record_event("session-2", "booking_commit", state)

    checkpoint = write_checkpoint.await_args.args[1]
    assert checkpoint["state"]["collected_data"] == {"name": "Ada"}
    assert "messages" not in checkpoint["state"]
    assert "memory_only_cache" not in checkpoint["state"]
    assert "transient_buffer" not in checkpoint["state"]


@pytest.mark.asyncio
async def test_changed_high_value_state_writes_again_after_debounce_window():
    write_checkpoint = AsyncMock()
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    now_value = {"value": base}
    manager = CheckpointManager(
        write_checkpoint=write_checkpoint,
        now=lambda: now_value["value"],
        debounce_seconds=30,
    )

    await manager.record_event("session-3", "field_capture", _base_state())

    now_value["value"] = base + timedelta(seconds=31)
    changed = _base_state()
    changed["collected_data"] = {"name": "Ada", "email": "ada@example.com"}
    await manager.record_event("session-3", "field_edit", changed)

    assert write_checkpoint.await_count == 2


@pytest.mark.asyncio
async def test_duplicate_summary_and_context_window_state_is_coalesced_deterministically():
    write_checkpoint = AsyncMock()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    manager = CheckpointManager(
        write_checkpoint=write_checkpoint,
        now=lambda: now,
        debounce_seconds=30,
    )

    state = _base_state()
    await manager.record_event("session-4", "field_capture", state)
    await manager.record_event("session-4", "field_capture", state)

    checkpoint = write_checkpoint.await_args.args[1]
    assert checkpoint["state"]["summary"] == "Known details: name=Ada"
    assert checkpoint["state"]["context_window"] == [
        {"role": "human", "content": "My name is Ada"}
    ]
    assert write_checkpoint.await_count == 1
