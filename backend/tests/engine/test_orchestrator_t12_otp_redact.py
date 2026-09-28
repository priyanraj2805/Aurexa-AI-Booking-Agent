import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage, HumanMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_otp_code_not_in_graph_or_transcript(monkeypatch):
    captured_inputs = {}
    async def _ainvoke(inputs, config=None):
        # Capture the HumanMessage content that would go to LLM
        msgs = inputs.get("messages", [])
        for m in msgs:
            if isinstance(m, HumanMessage):
                captured_inputs["human"] = m.content
        return {
            "messages": [AIMessage(content="ok")],
            "collected_fields": {},
            "follow_up_fields": ["name"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    # Mock OTP verification to be invalid, so early return path is taken
    # But we want to test the verified fallthrough path as well, so we will test invalid first
    # For invalid, we need to mock otp_crud.get_otp_policy_metadata and verify_otp
    mock_verify = AsyncMock(return_value=False)
    monkeypatch.setattr("app.crud.otp.verify_otp", mock_verify)
    monkeypatch.setattr("app.crud.otp.get_otp_policy_metadata", AsyncMock(return_value={"cooldown_remaining_seconds": 0}))

    chat_session = ChatSessionState(
        session_id="sess-t12",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={
            "field_definitions_snapshot": [{"name": "email", "type": "email", "label": "Email", "required": True, "order": 0}],
            "email": "a@b.com",
        },
        missing_fields=[],
        current_intent="PROVIDE_DATA",
        otp_verified=False,
    )
    # Mock org_config to have OTP required and phone pending to create _channels
    org_config = SimpleNamespace(timezone="UTC", require_otp=True, require_phone_otp=False)

    # Mock _is_awaiting_otp to True and _active_effect_state to have otp_sent
    with patch("app.engine.langgraph_orchestrator._is_awaiting_otp", return_value=True):
        with patch("app.engine.langgraph_orchestrator._active_effect_state", return_value={"otp_sent_keys": ["otp:test"]}):
            with patch("app.engine.langgraph_orchestrator.resolve_otp_verify_channels", return_value=["email"]):
                with patch("app.engine.langgraph_orchestrator._persist_transcript_best_effort", new=AsyncMock()) as mock_persist:
                    payload = await process_message_with_graph(
                        db=SimpleNamespace(execute=AsyncMock()),
                        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
                        org_config=org_config,
                        chat_session=chat_session,
                        message_text="123456",
                    )
                    # For invalid code, transcript should be redacted
                    assert mock_persist.await_count == 1
                    call_kwargs = mock_persist.call_args.kwargs
                    assert call_kwargs["user_message"] == "[verification code entered]"
                    assert "123456" not in call_kwargs["user_message"]
                    # For verified path, we need to test the graph input redaction - but invalid already tests transcript
                    # For graph input, we need a verified case that falls through to graph
                    # Let's test the verified path separately

@pytest.mark.asyncio
async def test_otp_code_redacted_in_graph_input(monkeypatch):
    captured = {}
    async def _ainvoke(inputs, config=None):
        for m in inputs.get("messages", []):
            if isinstance(m, HumanMessage):
                captured["human"] = m.content
        return {
            "messages": [AIMessage(content="ok")],
            "collected_fields": {},
            "follow_up_fields": ["name"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    monkeypatch.setattr("app.crud.otp.verify_otp", AsyncMock(return_value=True))
    # Mock get_chat_session to return a proper ChatSessionState-like object
    from app.schemas.agent import ChatSessionState as _CSS
    _mock_sess = _CSS(
        session_id="sess-t12b",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={},
        missing_fields=[],
        current_intent="PROVIDE_DATA",
        otp_verified=True,
        phone_otp_verified=True,
        session_status="ACTIVE",
        pending_cancel=False,
    )
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.get_chat_session", AsyncMock(return_value=_mock_sess))

    chat_session = ChatSessionState(
        session_id="sess-t12b",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={"field_definitions_snapshot": []},
        missing_fields=[],
        current_intent="PROVIDE_DATA",
        otp_verified=False,
        session_status="ACTIVE",
    )
    org_config = SimpleNamespace(timezone="UTC", require_otp=True, require_phone_otp=False)
    with patch("app.engine.langgraph_orchestrator._is_awaiting_otp", return_value=True):
        with patch("app.engine.langgraph_orchestrator._active_effect_state", return_value={"otp_sent_keys": ["otp:test"]}):
            with patch("app.engine.langgraph_orchestrator.resolve_otp_verify_channels", return_value=["email"]):
                with patch("app.engine.langgraph_orchestrator._persist_transcript_best_effort", new=AsyncMock()):
                    await process_message_with_graph(
                        db=SimpleNamespace(execute=AsyncMock()),
                        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
                        org_config=org_config,
                        chat_session=chat_session,
                        message_text="654321",
                    )
                    assert captured.get("human") == "[verification code entered]"
                    assert "654321" not in captured.get("human", "")
