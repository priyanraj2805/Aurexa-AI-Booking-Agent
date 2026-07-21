import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.crud import chat as crud_chat
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.schemas.agent import ChatSessionState


@pytest.mark.asyncio
async def test_web_runtime_emits_single_server_selected_directive(monkeypatch) -> None:
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Pick a date")],
            "collected_fields": {},
            "follow_up_fields": ["date"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-1",
        org_id="00000000-0000-0000-0000-000000000001",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "date", "type": "date", "label": "Preferred Date", "required": True, "order": 4}
            ]
        },
        missing_fields=["date"],
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="next",
        background_tasks=None,
    )

    assert payload.widget_directive == "REQUIRE_DATE_PICKER"
    assert payload.field_meta["name"] == "date"
    assert isinstance(payload.widget_directive, str)


@pytest.mark.asyncio
async def test_invalid_directive_metadata_falls_back_to_text_input_and_logs(
    monkeypatch, caplog
) -> None:
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Please provide your preference.")],
            "collected_fields": {},
            "follow_up_fields": ["priority"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-2",
        org_id="00000000-0000-0000-0000-000000000001",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "priority", "type": "select", "label": "Priority", "required": True, "options": []}
            ]
        },
        missing_fields=["priority"],
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="next",
        background_tasks=None,
    )

    assert payload.widget_directive == "TEXT_INPUT"
    assert payload.field_meta.get("directive_fallback") == "TEXT_INPUT"
    assert "directive_metadata_invalid" in caplog.text


@pytest.mark.asyncio
async def test_date_and_time_are_forced_to_end_of_prompt_sequence(
    db_session,
) -> None:
    org = Organization(name="Order Test Org", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    db_session.add(
        OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "date", "time"],
            field_definitions=[
                {"name": "date", "type": "date", "label": "Date", "required": True, "order": 0},
                {"name": "time", "type": "time", "label": "Time", "required": True, "order": 1},
                {"name": "name", "type": "text", "label": "Name", "required": True, "order": 2},
                {"name": "email", "type": "email", "label": "Email", "required": True, "order": 3},
                {"name": "company", "type": "text", "label": "Company", "required": True, "order": 4},
            ],
        )
    )
    await db_session.commit()

    created = await crud_chat.create_chat_session(db_session, str(org.id), channel="web")

    assert created.missing_fields == ["name", "email", "company", "date", "time"]


@pytest.mark.asyncio
async def test_deleted_custom_field_removed_for_new_sessions_and_legacy_snapshot_retained(
    db_session,
) -> None:
    org = Organization(name="Snapshot Test Org", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    org_config = OrgConfig(
        org_id=org.id,
        required_fields=["name", "email", "favorite_color", "date", "time"],
        field_definitions=[
            {"name": "name", "type": "text", "label": "Name", "required": True, "order": 0},
            {"name": "email", "type": "email", "label": "Email", "required": True, "order": 1},
            {
                "name": "favorite_color",
                "type": "text",
                "label": "Favorite Color",
                "required": True,
                "order": 2,
            },
            {"name": "date", "type": "date", "label": "Date", "required": True, "order": 3},
            {"name": "time", "type": "time", "label": "Time", "required": True, "order": 4},
        ],
    )
    db_session.add(org_config)
    await db_session.commit()

    session_before_delete = await crud_chat.create_chat_session(db_session, str(org.id), channel="web")
    session_before_delete.state_data = {
        **(session_before_delete.state_data or {}),
        "favorite_color": "Blue",
    }
    await db_session.commit()

    org_config.field_definitions = [
        {"name": "name", "type": "text", "label": "Name", "required": True, "order": 0},
        {"name": "email", "type": "email", "label": "Email", "required": True, "order": 1},
        {"name": "date", "type": "date", "label": "Date", "required": True, "order": 2},
        {"name": "time", "type": "time", "label": "Time", "required": True, "order": 3},
    ]
    org_config.required_fields = ["name", "email", "date", "time"]
    await db_session.commit()

    session_after_delete = await crud_chat.create_chat_session(db_session, str(org.id), channel="web")

    assert "favorite_color" not in session_after_delete.missing_fields
    snapshot_names = [
        item.get("name")
        for item in session_before_delete.state_data.get("field_definitions_snapshot", [])
        if isinstance(item, dict)
    ]
    assert "favorite_color" in snapshot_names
    assert session_before_delete.state_data["favorite_color"] == "Blue"


@pytest.mark.asyncio
async def test_email_validation_error_reuses_same_directive_with_directive_retry(
    monkeypatch,
) -> None:
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="That input looks invalid. Check the format and try again.")],
            "collected_fields": {},
            "follow_up_fields": ["email"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "validation_errors": {"email": "Please enter a valid email address."},
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-email-retry",
        org_id="00000000-0000-0000-0000-000000000001",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0}
            ]
        },
        missing_fields=["email"],
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="not-an-email",
        background_tasks=None,
    )

    assert payload.widget_directive == "REQUIRE_EMAIL_INPUT"
    assert payload.field_meta["validation_error"] == "Please enter a valid email address."
    assert payload.field_meta["directive_retry"] is True


@pytest.mark.asyncio
async def test_date_picker_directive_retry_for_invalid_time_free_text(
    monkeypatch,
) -> None:
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Please pick a valid time from the available slots.")],
            "collected_fields": {},
            "follow_up_fields": ["time"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "validation_errors": {"time": "Please select a valid time slot from the picker."},
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-time-retry",
        org_id="00000000-0000-0000-0000-000000000001",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "time", "type": "time", "label": "Time", "required": True, "order": 0}
            ]
        },
        missing_fields=["time"],
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="tomorrow",
        background_tasks=None,
    )

    assert payload.widget_directive == "REQUIRE_TIME_SLOT_GRID"
    assert payload.field_meta["validation_error"] == "Please select a valid time slot from the picker."
    assert payload.field_meta["directive_retry"] == "REQUIRE_TIME_SLOT_GRID"


@pytest.mark.asyncio
async def test_slot_unavailable_forces_time_slot_grid_reprompt_slot_unavailable(
    monkeypatch,
) -> None:
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="That slot is no longer available. Please pick another time.")],
            "collected_fields": {"date": "2026-05-15", "time": "14:00:00"},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "slot_unavailable": True,
            "refreshed_slots": [
                {"value": "2026-05-15T15:00:00Z", "label": "3:00 PM"},
                {"value": "2026-05-15T16:00:00Z", "label": "4:00 PM"},
            ],
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-slot-unavailable",
        org_id="00000000-0000-0000-0000-000000000001",
        current_intent="CONFIRM_BOOKING",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "time", "type": "time", "label": "Time", "required": True, "order": 0}
            ],
            "date": "2026-05-15",
            "time": "14:00:00",
        },
        missing_fields=[],
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="confirm",
        background_tasks=None,
    )

    assert payload.widget_directive == "REQUIRE_TIME_SLOT_GRID"
    assert payload.field_meta["directive_retry"] == "REQUIRE_TIME_SLOT_GRID"
    assert payload.field_meta["options"] == [
        {"value": "2026-05-15T15:00:00Z", "label": "3:00 PM"},
        {"value": "2026-05-15T16:00:00Z", "label": "4:00 PM"},
    ]


@pytest.mark.asyncio
async def test_active_path_sanitizes_internal_think_tags_from_user_message(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="<think>internal chain of thought</think>Please pick a date.")],
            "collected_fields": {},
            "follow_up_fields": ["date"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-think-sanitized",
        org_id="00000000-0000-0000-0000-000000000001",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "date", "type": "date", "label": "Date", "required": True, "order": 0}
            ]
        },
        missing_fields=["date"],
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="next",
        background_tasks=None,
    )

    assert payload.message == "Please pick a date."
    assert "<think>" not in payload.message.lower()


@pytest.mark.asyncio
async def test_active_path_prefers_response_text_when_model_returns_structured_json(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [
                AIMessage(
                    content=json.dumps(
                        {
                            "response_text": "Hello! How can I assist you today?",
                            "extracted_fields": {},
                            "user_intent": "proceed",
                            "confidence": 0.7,
                        }
                    )
                )
            ],
            "collected_fields": {},
            "follow_up_fields": ["date"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(),
        chat_session=ChatSessionState(
            session_id="session-structured-response",
            org_id="00000000-0000-0000-0000-000000000001",
            collected_data={
                "field_definitions_snapshot": [
                    {"name": "date", "type": "date", "label": "Date", "required": True, "order": 0}
                ]
            },
            missing_fields=["date"],
        ),
        message_text="hi",
        background_tasks=None,
    )

    assert payload.message == "Hello! How can I assist you today?"
    assert "response_text" not in payload.message
    assert payload.field_meta["llm_structured_response"]["user_intent"] == "proceed"


@pytest.mark.asyncio
async def test_active_path_confirm_keyword_routes_through_booking_gate(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="🎉 Your booking is confirmed!")],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }

    booking_gate = AsyncMock(
        return_value=(
            "Please enter the OTP sent to your email to continue.",
            "REQUIRE_OTP_MODAL",
            {"resends_left": 3},
        )
    )
    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.message_processor._handle_booking_flow",
        booking_gate,
    )
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=True),
        chat_session=ChatSessionState(
            session_id="session-hard-gate",
            org_id="00000000-0000-0000-0000-000000000001",
            current_intent="START_BOOKING",
            collected_data={
                "field_definitions_snapshot": [
                    {"name": "name", "type": "text", "label": "Name", "required": True, "order": 0},
                    {"name": "email", "type": "email", "label": "Email", "required": True, "order": 1},
                    {"name": "phone", "type": "phone", "label": "Phone", "required": True, "order": 2},
                    {"name": "date", "type": "date", "label": "Date", "required": True, "order": 3},
                    {"name": "time", "type": "time", "label": "Time", "required": True, "order": 4},
                ],
                "name": "Jane Customer",
                "email": "customer@example.com",
                "phone": "+15555551234",
                "date": "2026-05-20",
                "time": "14:00:00",
            },
            missing_fields=[],
        ),
        message_text="yes confirm booking",
        background_tasks=None,
    )

    assert booking_gate.await_count == 1
    assert payload.widget_directive == "REQUIRE_OTP_MODAL"
    assert payload.message == "Please enter the OTP sent to your email to continue."
    assert "confirmed" not in payload.message.lower()


@pytest.mark.asyncio
async def test_active_path_blocks_false_confirmation_text_when_more_input_needed(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="🎉 Your booking is confirmed!")],
            "collected_fields": {},
            "follow_up_fields": ["time"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=True),
        chat_session=ChatSessionState(
            session_id="session-no-false-confirm",
            org_id="00000000-0000-0000-0000-000000000001",
            current_intent="START_BOOKING",
            collected_data={
                "field_definitions_snapshot": [
                    {"name": "time", "type": "time", "label": "Time", "required": True, "order": 0}
                ],
            },
            missing_fields=["time"],
        ),
        message_text="yes",
        background_tasks=None,
    )

    assert payload.widget_directive == "REQUIRE_TIME_SLOT_GRID"
    assert "confirmed" not in payload.message.lower()
    assert "choose your preferred time" in payload.message.lower()

