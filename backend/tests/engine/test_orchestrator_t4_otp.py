import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_otp_not_marked_sent_when_no_email(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="confirm")],
            "collected_fields": {"name": "Bob", "email": "", "phone": "123", "date": "2026-10-10", "time": "10:00"},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "CONFIRM_BOOKING",
            "otp_event_key": "otp:test",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())
    monkeypatch.setattr("app.crud.otp.generate_otp", AsyncMock(return_value="123456"))
    monkeypatch.setattr("app.crud.otp.get_otp_policy_metadata", AsyncMock(return_value={}))

    # Mock resolve to return empty email
    with patch("app.engine.langgraph_orchestrator.resolve_email_from_collected_data", return_value=""):
        chat_session = ChatSessionState(
            session_id="sess-t4",
            org_id="00000000-0000-0000-0000-000000000001",
            channel="web",
            collected_data={
                "field_definitions_snapshot": [
                    {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
                    {"name": "name", "type": "text", "label": "Name", "required": True, "order": 1},
                ],
                "email": "",
                "name": "Bob",
                "date": "2026-10-10",
                "time": "10:00",
            },
            missing_fields=[],
            current_intent="CONFIRM_BOOKING",
            otp_verified=False,
        )
        # Need to ensure org_config requires OTP
        org_config = SimpleNamespace(timezone="UTC", require_otp=True, require_phone_otp=False, field_definitions=[], custom_prompts={})
        # Mock StateEvaluator to return PROCESS_BOOKING so otp path is taken
        with patch("app.engine.evaluator.StateEvaluator.determine_next_action", return_value={"directive": "PROCESS_BOOKING", "field_meta": {}}):
            payload = await process_message_with_graph(
                db=SimpleNamespace(execute=AsyncMock()),
                org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
                org_config=org_config,
                chat_session=chat_session,
                message_text="confirm",
            )
            # Should be OTP modal with validation error, and not marked sent
            assert payload.widget_directive == "REQUIRE_OTP_MODAL"
            assert payload.field_meta.get("validation_error") == "We couldn't find an email address to send the code to."
            # Check that sent_keys does not contain the event key
            # The effect state is stored in collected_data["_active_path_effects"]["otp_sent_keys"]
            effect = chat_session.collected_data.get("_active_path_effects", {})
            sent = effect.get("otp_sent_keys", [])
            assert "otp:test" not in sent
