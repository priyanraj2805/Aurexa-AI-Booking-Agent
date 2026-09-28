import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState
from app.crud import chat as crud_chat

@pytest.mark.asyncio
async def test_bare_change_saves_transcript_with_correct_roles(monkeypatch):
    # Graph returns no collected fields, no follow up, so bare change branch is taken
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Sure")],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            # No current_intent so _graph_verdict is None -> _graph_undecided True
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    mock_update = AsyncMock()
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", mock_update)
    # Mock the transcript helper to capture calls
    mock_persist = AsyncMock()
    monkeypatch.setattr("app.engine.langgraph_orchestrator._persist_transcript_best_effort", mock_persist)
    # Also need to mock save_session_checkpoint
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    # Prevent contact enforcement from adding phone/email when follow_up is empty
    monkeypatch.setattr("app.engine.langgraph_orchestrator._enforce_runtime_contact_requirements", lambda a,b,c: a)

    chat_session = ChatSessionState(
        session_id="sess-t3",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="whatsapp",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "name", "type": "text", "label": "Name", "required": True, "order": 0},
            ],
            "name": "Alice",
        },
        missing_fields=[],
        current_intent="START_BOOKING",
    )
    # Ensure ChatSessionState has channel attribute used in _is_whatsapp check
    # It does via collected_data? Actually channel is separate field in ChatSessionState
    # Our ChatSessionState may not have channel, but process_message_with_graph checks chat_session.channel
    # So set it
    chat_session.channel = "whatsapp"

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False),
        chat_session=chat_session,
        message_text="change",
    )
    # Should return the picker
    assert payload.widget_directive == "TEXT_INPUT"
    assert "which field" in payload.message.lower()
    # Verify correct transcript call: user_message is "change", assistant is picker, roles handled inside helper
    # Our mock should have been called once with correct args
    assert mock_persist.await_count == 1
    call_kwargs = mock_persist.call_args.kwargs
    assert call_kwargs["user_message"] == "change"
    assert "which field" in call_kwargs["assistant_message"].lower()
    assert call_kwargs["directive"] == "TEXT_INPUT"
    assert call_kwargs["intent"] == "EDIT_DATA"
    # Verify update was called before return
    assert mock_update.await_count >= 1
