import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import _whatsapp_hours_prefix, process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_whatsapp_hours_prefix_returns_empty_on_failure():
    # loader raises -> should return ""
    with patch("app.engine.scheduling_policy.load_weekly_hours_block", side_effect=Exception("fail")):
        result = await _whatsapp_hours_prefix(db=SimpleNamespace(), org=SimpleNamespace(id=uuid.uuid4()))
        assert result == ""

@pytest.mark.asyncio
async def test_whatsapp_hours_prefix_returns_empty_on_missing_line():
    async def fake_load(db, org_id):
        return "No valid hours"
    with patch("app.engine.scheduling_policy.load_weekly_hours_block", side_effect=fake_load):
        result = await _whatsapp_hours_prefix(db=SimpleNamespace(), org=SimpleNamespace(id=uuid.uuid4()))
        assert result == ""

@pytest.mark.asyncio
async def test_whatsapp_hours_prefix_returns_empty_on_too_long():
    async def fake_load(db, org_id):
        return "Monday: " + "x"*200
    with patch("app.engine.scheduling_policy.load_weekly_hours_block", side_effect=fake_load):
        result = await _whatsapp_hours_prefix(db=SimpleNamespace(), org=SimpleNamespace(id=uuid.uuid4()))
        assert result == ""

@pytest.mark.asyncio
async def test_whatsapp_hours_not_prepended_when_loader_fails(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Please pick a date")],
            "collected_fields": {},
            "follow_up_fields": ["date"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    # loader fails -> prefix "" -> assistant_message unchanged
    with patch("app.engine.scheduling_policy.load_weekly_hours_block", side_effect=Exception("fail")):
        chat_session = ChatSessionState(
            session_id="sess-1",
            org_id="00000000-0000-0000-0000-000000000001",
            channel="whatsapp",
            collected_data={"field_definitions_snapshot": [{"name": "date", "type": "date", "label": "Date", "required": True, "order": 0}]},
            missing_fields=["date"],
        )
        payload = await process_message_with_graph(
            db=SimpleNamespace(),
            org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
            org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False),
            chat_session=chat_session,
            message_text="hi",
        )
        # Should not contain hardcoded fallback and should not have Hours: twice
        assert "Asia/Calcutta" not in payload.message
        assert payload.message.count("Hours:") <= 1
