import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_change_with_negation_does_not_reopen(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content='{"response_text": "ok", "user_intent": "proceed", "extracted_fields": {}}')],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "PROVIDE_DATA",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator._enforce_runtime_contact_requirements", lambda a,b,c: a)

    chat_session = ChatSessionState(
        session_id="sess-t9-neg",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
                {"name": "name", "type": "text", "label": "Name", "required": True, "order": 1},
            ],
            "email": "a@b.com",
            "name": "Bob",
        },
        missing_fields=[],
        current_intent="PROVIDE_DATA",
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(execute=AsyncMock()),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False, field_definitions=[], custom_prompts={}),
        chat_session=chat_session,
        message_text="I don't want to change my email",
    )
    # Should not have reopened email - missing should still be [] and not ask for email
    assert "email" not in (chat_session.missing_fields or [])
    assert chat_session.collected_data.get("email") == "a@b.com"

@pytest.mark.asyncio
async def test_change_without_negation_does_reopen(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content='{"response_text": "ok", "user_intent": "proceed", "extracted_fields": {}}')],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "PROVIDE_DATA",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator._enforce_runtime_contact_requirements", lambda a,b,c: a)

    chat_session = ChatSessionState(
        session_id="sess-t9-pos",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
            ],
            "email": "a@b.com",
        },
        missing_fields=[],
        current_intent="PROVIDE_DATA",
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(execute=AsyncMock()),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False, field_definitions=[], custom_prompts={}),
        chat_session=chat_session,
        message_text="change my email",
    )
    assert "email" in (chat_session.missing_fields or [])
    assert "email" not in chat_session.collected_data

@pytest.mark.asyncio
async def test_reopen_fields_preferred_over_keywords(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content='{"response_text": "ok", "user_intent": "proceed", "extracted_fields": {}, "reopen_fields": ["phone"]}')],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "PROVIDE_DATA",
            "reopen_fields": ["phone"],
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator._enforce_runtime_contact_requirements", lambda a,b,c: a)

    chat_session = ChatSessionState(
        session_id="sess-t9-reopen",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "phone", "type": "phone", "label": "Phone", "required": True, "order": 0},
                {"name": "email", "type": "email", "label": "Email", "required": True, "order": 1},
            ],
            "phone": "1234567890",
            "email": "a@b.com",
        },
        missing_fields=[],
        current_intent="PROVIDE_DATA",
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(execute=AsyncMock()),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False, field_definitions=[], custom_prompts={}),
        chat_session=chat_session,
        message_text="please update my contact",
    )
    # Should have used reopen_fields phone, not keyword email
    assert "phone" in (chat_session.missing_fields or [])
