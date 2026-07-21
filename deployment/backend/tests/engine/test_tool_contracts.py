from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.agent import ActionPayload


def _tool_context(session_id: str = "session-1") -> dict:
    return {
        "db": SimpleNamespace(),
        "session_id": session_id,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }


def test_tool_contract_rejects_missing_or_invalid_args_with_deterministic_shape() -> None:
    from app.engine.agent import tool_contracts

    missing = tool_contracts.validate_field_save_args({"field_name": "", "value": "Ada"})
    assert missing == {
        "ok": False,
        "error": {
            "code": "INVALID_TOOL_ARGS",
            "tool": "save_field_value",
            "issues": [{"field": "field_name", "reason": "required"}],
        },
    }

    invalid = tool_contracts.validate_booking_create_args(
        {"target_date": "not-a-date", "time": ""}
    )
    assert invalid == {
        "ok": False,
        "error": {
            "code": "INVALID_TOOL_ARGS",
            "tool": "create_booking_record",
            "issues": [
                {"field": "target_date", "reason": "invalid_date"},
                {"field": "time", "reason": "required"},
            ],
        },
    }


def test_tool_contract_normalization_is_deterministic_for_identical_input() -> None:
    from app.engine.agent import tool_contracts

    payload = {"field_name": " Email ", "value": "  Ada@Test.com "}
    first = tool_contracts.validate_field_save_args(payload)
    second = tool_contracts.validate_field_save_args(payload)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["normalized"] == second["normalized"]
    assert first["normalized"] == {"field_name": "email", "value": "Ada@Test.com"}


@pytest.mark.asyncio
async def test_replayed_booking_commit_is_idempotent_per_session_and_intent(monkeypatch) -> None:
    from app.engine.agent.tools import booking

    get_session = AsyncMock(
        return_value=SimpleNamespace(
            collected_data={"name": "Ada", "email": "ada@test.com"},
            user_timezone="UTC",
        )
    )
    update_session = AsyncMock()
    create_lock = AsyncMock(return_value=SimpleNamespace(id="slot-1"))
    attempt_booking = AsyncMock(return_value=SimpleNamespace(id="booking-1"))

    monkeypatch.setattr("app.engine.agent.tools.booking.get_chat_session", get_session)
    monkeypatch.setattr("app.engine.agent.tools.booking.update_chat_session", update_session)
    monkeypatch.setattr("app.engine.agent.tools.booking.create_timeslot_lock", create_lock)
    monkeypatch.setattr("app.engine.agent.tools.booking.attempt_booking", attempt_booking)

    config = {"configurable": _tool_context()}

    first = await booking.create_booking_record.ainvoke(
        {"target_date": "2026-05-10", "time": "10:00"}, config=config
    )
    second = await booking.create_booking_record.ainvoke(
        {"target_date": "2026-05-10", "time": "10:00"}, config=config
    )

    assert first.startswith("SUCCESS:")
    assert second.startswith("SUCCESS:")
    assert attempt_booking.await_count == 1


@pytest.mark.asyncio
async def test_runtime_route_preserves_action_payload_shape_after_tool_contract_hooks(
    monkeypatch,
) -> None:
    from app.engine import runtime_router

    async def _graph(**_kwargs):
        return ActionPayload(
            message="all good",
            widget_directive="TEXT_INPUT",
            options=None,
            field_meta={"captured_deltas": {"email": "ada@test.com"}},
            is_terminal=False,
        )

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _graph)

    payload = await runtime_router.route_web_message(
        db=SimpleNamespace(),
        org=SimpleNamespace(id="org-1"),
        org_config=SimpleNamespace(),
        chat_session=SimpleNamespace(session_id="session-1"),
        message_text="hello",
        background_tasks=None,
    )

    assert isinstance(payload, ActionPayload)
    assert set(payload.model_dump().keys()) == {
        "message",
        "widget_directive",
        "options",
        "field_meta",
        "is_terminal",
    }
