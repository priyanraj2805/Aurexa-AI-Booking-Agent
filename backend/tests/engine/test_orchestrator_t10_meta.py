import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_whatsapp_text_slots_preserves_captured_deltas(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="pick time")],
            "collected_fields": {"date": "2026-10-10"},
            "follow_up_fields": ["time"],
            "captured_deltas": {"date": "2026-10-10"},
            "merge_diagnostics": {"source": "test"},
            "summary": "",
            "context_window": [],
            "current_intent": "PROVIDE_DATA",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())

    # Mock slot inventory to have slots
    async def _fake_inventory(db, org_id, target_date, org_config=None):
        from datetime import datetime, timezone
        start = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        return ([{"available": True, "start_utc": start}], "ok")

    # Mock load_weekly_hours to avoid hours prefix
    with patch("app.engine.scheduling_policy.load_weekly_hours_block", new=AsyncMock(return_value="")):
        with patch("app.engine.slot_engine.compute_slot_inventory", side_effect=_fake_inventory):
            with patch("app.engine.slot_engine.apply_scheduling_policy_to_inventory", side_effect=lambda inv, d, **kw: (inv, "ok", None)):
                # Mock BusinessSchedule query
                async def _fake_exec(*args, **kwargs):
                    from unittest.mock import MagicMock
                    m = MagicMock()
                    m.scalars.return_value.first.return_value = None
                    m.scalars.return_value.all.return_value = []
                    return m
                fake_db = SimpleNamespace(execute=_fake_exec)
                chat_session = ChatSessionState(
                    session_id="sess-t10",
                    org_id="00000000-0000-0000-0000-000000000001",
                    channel="whatsapp",
                    collected_data={
                        "field_definitions_snapshot": [
                            {"name": "date", "type": "date", "label": "Date", "required": True, "order": 0},
                            {"name": "time", "type": "time", "label": "Time", "required": True, "order": 1},
                        ],
                        "date": "2026-10-10",
                    },
                    missing_fields=["time"],
                    current_intent="PROVIDE_DATA",
                )
                payload = await process_message_with_graph(
                    db=fake_db,
                    org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
                    org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False, field_definitions=[], custom_prompts={}),
                    chat_session=chat_session,
                    message_text="2026-10-10",
                )
                # After fix, captured_deltas and merge_diagnostics should survive in field_meta
                assert payload.field_meta.get("captured_deltas") == {"date": "2026-10-10"}
                assert payload.field_meta.get("merge_diagnostics") == {"source": "test"}
                # Also check that whatsapp_text_slots is set but not overwriting
                assert payload.field_meta.get("whatsapp_text_slots") is True
                assert payload.field_meta.get("name") == "time"
