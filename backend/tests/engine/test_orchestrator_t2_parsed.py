import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
import uuid
from langchain_core.messages import AIMessage
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

@pytest.mark.asyncio
async def test_whatsapp_select_slot_sets_description_with_iso_date(monkeypatch):
    # Mock graph to trigger REQUIRE_TIME_SLOT_GRID with no description
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="pick time")],
            "collected_fields": {"date": "2026-10-10"},
            "follow_up_fields": ["time"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "PROVIDE_DATA",
        }
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock())

    # Mock slot inventory to return one available slot so _slot_labels is non-empty and description branch is taken
    async def _fake_inventory(db, org_id, target_date, org_config=None):
        # target_date should be date 2026-10-10
        assert str(target_date) == "2026-10-10"
        # Return one available slot
        from datetime import datetime, timezone
        start = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
        return ([{"available": True, "start_utc": start}], "ok")

    # Mock _format_time_prompt_for_whatsapp to capture iso date
    captured = {}
    def _fake_format(target_iso, tz, slot_mins, weekly_schedule=None, exceptions=None):
        captured["target_iso"] = target_iso
        captured["called"] = True
        return "Hours: 9-5"

    with patch("app.engine.slot_engine.compute_slot_inventory", side_effect=_fake_inventory):
        with patch("app.engine.slot_engine.apply_scheduling_policy_to_inventory", side_effect=lambda inv, d, **kw: (inv, "ok", None)):
            with patch("app.engine.message_processor._format_time_prompt_for_whatsapp", side_effect=_fake_format):
                # Mock DB for BusinessSchedule/ScheduleDay queries to avoid needing real DB
                # The code does db.execute for BusinessSchedule etc., but our fake inventory already provides slots,
                # and the description branch will still try to query BusinessSchedule for slot_duration.
                # Patch db.execute to return empty for those.
                async def _fake_execute(*args, **kwargs):
                    mock_res = MagicMock()
                    mock_res.scalars.return_value.all.return_value = []
                    mock_res.scalars.return_value.first.return_value = None
                    return mock_res
                fake_db = SimpleNamespace(execute=_fake_execute)

                chat_session = ChatSessionState(
                    session_id="sess-t2",
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
                )
                # Mock load_weekly_hours_block to avoid hours prefix interference
                with patch("app.engine.scheduling_policy.load_weekly_hours_block", new=AsyncMock(return_value="")):
                    payload = await process_message_with_graph(
                        db=fake_db,
                        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
                        org_config=SimpleNamespace(timezone="UTC", require_otp=False, require_phone_otp=False),
                        chat_session=chat_session,
                        message_text="2026-10-10",
                    )
                    # After fix, description should be set via _format_time_prompt_for_whatsapp with iso date
                    assert captured.get("called") is True
                    assert captured.get("target_iso") == "2026-10-10"
                    # Also check that no dead expression left (tomorrow literal) - the code should not have raised AttributeError
                    assert payload is not None
