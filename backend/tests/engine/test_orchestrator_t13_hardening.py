import logging
import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_graph_timeout_returns_safe_payload(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.ERROR)
    async def _slow_ainvoke(*args, **kwargs):
        await asyncio.sleep(0.2)
        return {"messages": [AIMessage(content="hi")]}

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _slow_ainvoke)
    # Patch asyncio.wait_for to simulate timeout quickly
    original_wait = asyncio.wait_for
    async def _fake_wait(coro, timeout):
        raise asyncio.TimeoutError()
    monkeypatch.setattr("app.engine.langgraph_orchestrator.asyncio.wait_for", _fake_wait)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    mock_persist = AsyncMock()
    monkeypatch.setattr("app.engine.langgraph_orchestrator._persist_transcript_best_effort", mock_persist)

    chat_session = ChatSessionState(
        session_id="sess-t13",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={"field_definitions_snapshot": [{"name": "name", "type": "text", "label": "Name", "required": True, "order": 0}]},
        missing_fields=["name"],
        current_intent="START_BOOKING",
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False),
        chat_session=chat_session,
        message_text="hello",
    )
    assert payload.widget_directive == "TEXT_INPUT"
    assert "internal error" in payload.message.lower()
    # Transcript should be saved with redacted? Not OTP, so original
    assert mock_persist.await_count == 1
    # Check log contains error
    assert any("booking_agent ainvoke failed" in r.getMessage() for r in caplog.records)

@pytest.mark.asyncio
async def test_post_booking_llm_failure_logs_warning(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    # Force _llm_post_booking_analysis to raise and be caught
    with patch("app.engine.langgraph_orchestrator.message_processor.llm_engine._call_llm", side_effect=Exception("LLM down")):
        from app.engine.langgraph_orchestrator import _llm_post_booking_analysis
        result = await _llm_post_booking_analysis("hi", "CONFIRMED", False, {})
        assert result["classification"] == "NEUTRAL"
        assert any("post_booking LLM analysis failed" in r.getMessage() for r in caplog.records)
