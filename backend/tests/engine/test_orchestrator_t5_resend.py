import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from app.engine.langgraph_orchestrator import _handle_graph_otp_resend
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_handle_graph_otp_resend_calls_once_on_exception(monkeypatch):
    # Setup org and session with phone pending
    org = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"))
    chat_session = ChatSessionState(
        session_id="sess-t5",
        org_id=str(org.id),
        channel="whatsapp",
        collected_data={},
        missing_fields=[],
        otp_verified=False,
        phone_otp_verified=False,
    )
    # Mock org_config to have phone required
    org_config = SimpleNamespace(require_otp=False, require_phone_otp=True)

    mock_resend = AsyncMock(side_effect=Exception("send failed"))
    monkeypatch.setattr("app.engine.message_processor._handle_otp_resend", mock_resend)

    # Should propagate exception and be called exactly once
    with pytest.raises(Exception, match="send failed"):
        await _handle_graph_otp_resend(
            db=SimpleNamespace(),
            org=org,
            chat_session=chat_session,
            message_text="resend",
            background_tasks=None,
            org_config=org_config,
        )
    assert mock_resend.await_count == 1

@pytest.mark.asyncio
async def test_handle_graph_otp_resend_skips_db_when_org_config_given(monkeypatch):
    org = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"))
    chat_session = ChatSessionState(
        session_id="sess-t5b",
        org_id=str(org.id),
        channel="web",
        collected_data={},
        missing_fields=[],
        otp_verified=False,
    )
    org_config = SimpleNamespace(require_otp=True, require_phone_otp=False)
    mock_resend = AsyncMock(return_value=SimpleNamespace(message="ok", widget_directive="TEXT_INPUT"))
    monkeypatch.setattr("app.engine.message_processor._handle_otp_resend", mock_resend)
    # DB should not be queried when org_config is given - patch db.execute to fail if called
    mock_db = SimpleNamespace(execute=AsyncMock(side_effect=Exception("should not be called")))
    result = await _handle_graph_otp_resend(
        db=mock_db,
        org=org,
        chat_session=chat_session,
        message_text="resend",
        background_tasks=None,
        org_config=org_config,
    )
    assert mock_resend.await_count == 1
    # Ensure db.execute was not called (since org_config was provided)
    # If it was called, our mock would have raised, but it didn't, so it was skipped
