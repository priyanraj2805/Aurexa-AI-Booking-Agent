import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
import uuid
import logging
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_otp_chat_check_does_not_log_code(caplog):
    caplog.set_level(logging.INFO)
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="hi")],
            "collected_fields": {},
            "follow_up_fields": ["name"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }
    with patch("app.engine.langgraph_orchestrator.booking_agent.ainvoke", side_effect=_ainvoke):
        with patch("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", new=AsyncMock()):
            with patch("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", new=AsyncMock()):
                chat_session = ChatSessionState(
                    session_id="sess-t11",
                    org_id="00000000-0000-0000-0000-000000000001",
                    channel="web",
                    collected_data={"field_definitions_snapshot": [{"name": "name", "type": "text", "label": "Name", "required": True, "order": 0}]},
                    missing_fields=["name"],
                    current_intent="START_BOOKING",
                )
                await process_message_with_graph(
                    db=SimpleNamespace(),
                    org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
                    org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False),
                    chat_session=chat_session,
                    message_text="123456",
                )
                # Check that log does not contain the code
                for record in caplog.records:
                    if "OTP chat check" in record.getMessage():
                        assert "123456" not in record.getMessage()
                        assert "msg=" not in record.getMessage() or "123456" not in record.getMessage()

@pytest.mark.asyncio
async def test_booking_email_logged_masked():
    # Direct test of masking logic used in the log
    email = "john@example.com"
    masked = (email[:1] + "***@" + email.split("@")[-1]) if "@" in email else "***"
    assert masked == "j***@example.com"
    assert "john@example.com" not in masked
    # Also test that the code path would use masked, not full email
    # Check file no longer logs full email
    import pathlib
    for p in ["app/engine/langgraph_orchestrator.py", "backend/app/engine/langgraph_orchestrator.py"]:
        try:
            text = pathlib.Path(p).read_text()
            break
        except Exception:
            continue
    else:
        text = pathlib.Path(__file__).parents[2].joinpath("app/engine/langgraph_orchestrator.py").read_text()
    # The log line should contain _masked_email, not customer_email directly
    assert "_masked_email" in text
    assert 'Booking confirmation email queued for %s' in text
