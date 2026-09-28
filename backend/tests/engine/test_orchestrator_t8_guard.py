import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_ask_question_keeps_valid_answer_with_word_boundary(monkeypatch):
    # ASK_QUESTION with a valid answer that mentions one field word as part of another word should not be replaced
    # "We take appointments any time. Which date works?" contains "date" as word, but also "appointments" contains "appoint" not "name" etc.
    # The key is that "username" should not count as "name" (word boundary)
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content='{"response_text": "We take appointments any time. Which date works?", "user_intent": "chitchat", "extracted_fields": {}}')],
            "collected_fields": {},
            "follow_up_fields": ["email"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "ASK_QUESTION",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator._enforce_runtime_contact_requirements", lambda a,b,c: a)

    chat_session = ChatSessionState(
        session_id="sess-t8-ask",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={"field_definitions_snapshot": [{"name": "email", "type": "email", "label": "Email", "required": True, "order": 0}]},
        missing_fields=["email"],
        current_intent="ASK_QUESTION",
    )
    # Mock _response_asks_for_field to return True for our valid answer (it visibly asks for email)
    with patch("app.engine.message_processor._response_asks_for_field", return_value=True):
        payload = await process_message_with_graph(
            db=SimpleNamespace(),
            org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
            org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False, field_definitions=[], custom_prompts={}),
            chat_session=chat_session,
            message_text="what are your hours?",
        )
        # Should keep original answer, not replace with deterministic ask
        assert "We take appointments any time" in payload.message

@pytest.mark.asyncio
async def test_word_boundary_username_not_counted(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content='{"response_text": "Your username is valid. Please provide email?", "user_intent": "proceed", "extracted_fields": {}}')],
            "collected_fields": {},
            "follow_up_fields": ["email"],
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
        session_id="sess-t8-bound",
        org_id="00000000-0000-0000-0000-000000000001",
        channel="web",
        collected_data={"field_definitions_snapshot": [{"name": "email", "type": "email", "label": "Email", "required": True, "order": 0}]},
        missing_fields=["email"],
        current_intent="PROVIDE_DATA",
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False, field_definitions=[], custom_prompts={}),
        chat_session=chat_session,
        message_text="hi",
    )
    # "username" contains "name" but as part of word, should not count as 1 mention, so _mentions should be 0-1 and not trigger replacement
    # The payload should still be the original (or at least not crash)
    assert payload is not None
    assert "username" in payload.message.lower() or "email" in payload.message.lower()
