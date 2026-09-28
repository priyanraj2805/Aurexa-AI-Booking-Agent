import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.engine.agent.graph import call_model
from app.engine.langgraph_orchestrator import (
    _enforce_runtime_contact_requirements,
    process_message_with_graph,
)
from app.schemas.agent import ChatSessionState


def _chat_session(*, session_id: str = "session-active-path") -> ChatSessionState:
    return ChatSessionState(
        session_id=session_id,
        org_id="00000000-0000-0000-0000-000000000001",
        current_intent="CONFIRM_BOOKING",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
                {"name": "phone", "type": "phone", "label": "Phone", "required": True, "order": 1},
            ],
            "email": "customer@example.com",
            "name": "Jane Customer",
            "phone": "+15555551234",
            "date": "2026-05-20",
            "time": "14:00:00",
        },
        missing_fields=[],
    )


@pytest.mark.asyncio
async def test_active_path_validation_calls_backend_validator_before_progression(monkeypatch):
    field_defs = [
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.llm",
        SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    content='{"extracted_fields":{"email":"bad-email","phone":"123"},"invalid_fields":{}}'
                )
            )
        ),
    )

    validator_calls = {"count": 0}

    def _validate_entities(*_args, **_kwargs):
        validator_calls["count"] += 1
        return (
            {},
            {"email": "Invalid email format", "phone": "Please provide a valid phone number."},
            {},
        )

    monkeypatch.setattr(
        "app.engine.validators.DataValidator.validate_entities",
        _validate_entities,
    )

    result = await call_model(
        {
            "messages": [HumanMessage(content="my email is bad-email and phone 123")],
            "summary": "",
            "session_id": "session-validation",
            "org_id": "org-1",
            "org_config": {},
            "collected_fields": {},
            "user_timezone": "UTC",
            "optional_skips": [],
            "pending_conflict": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "merge_diagnostics": {},
        },
        config={"configurable": {"org_config": {}, "db": SimpleNamespace()}},
    )

    assert validator_calls["count"] == 1
    assert result["validation_errors"]["email"] == "Invalid email format"
    assert result["validation_errors"]["phone"] == "Please provide a valid phone number."
    assert "email" in result["follow_up_fields"]
    assert "phone" in result["follow_up_fields"]


@pytest.mark.asyncio
async def test_real_path_signal_emits_otp_required_and_otp_event_key_without_synthetic_ainvoke_flags(monkeypatch):
    field_defs = [
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone", "required": True},
        {"name": "date", "type": "date", "required": True},
        {"name": "time", "type": "time", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.llm",
        SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    content='{"response_text":"Ready to confirm.","extracted_fields":{},"invalid_fields":{}}'
                )
            )
        ),
    )

    result = await call_model(
        {
            "messages": [HumanMessage(content="confirm booking")],
            "summary": "",
            "session_id": "session-real-otp",
            "org_id": "org-1",
            "org_config": {"require_otp": True},
            "collected_fields": {
                "email": "customer@example.com",
                "phone": "+15555551234",
                "date": "2026-05-20",
                "time": "14:00",
            },
            "current_intent": "CONFIRM_BOOKING",
            "otp_verified": False,
            "user_timezone": "UTC",
            "optional_skips": [],
            "pending_conflict": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "merge_diagnostics": {},
        },
        config={"configurable": {"org_config": {"require_otp": True}, "db": SimpleNamespace()}},
    )

    assert result["otp_required"] is True
    assert result["otp_event_key"].startswith("otp:session-real-otp:")
    assert "otp_code" in result
    assert result["otp_code"] == ""


@pytest.mark.asyncio
async def test_no_ainvoke_confirmation_flag_uses_commit_derived_confirmation_event_key_and_confirmation_provenance(monkeypatch):
    field_defs = [
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone", "required": True},
        {"name": "date", "type": "date", "required": True},
        {"name": "time", "type": "time", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.llm",
        SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    content='{"response_text":"Proceeding.","extracted_fields":{},"invalid_fields":{}}'
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    async def _booking_gate(_db, _org, _org_config, chat_session, *_args, **_kwargs):
        chat_session.session_status = "CONFIRMED"
        chat_session.collected_data["booking_id"] = "booking-123"
        return ("Booking confirmed!", "BOOKING_CONFIRMED_CARD", {"email_retrying": True})

    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.message_processor._handle_booking_flow",
        AsyncMock(side_effect=_booking_gate),
    )
    send_confirmation = AsyncMock()
    monkeypatch.setattr("app.core.mail.send_confirmation_emails", send_confirmation)

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=_chat_session(),
        message_text="confirm booking",
        background_tasks=None,
    )

    assert send_confirmation.await_count == 1
    assert payload.field_meta["booking_confirmed"] is True
    assert payload.field_meta["confirmation_event_key"] == "confirmation:session-active-path:booking-123"
    assert payload.field_meta["confirmation_provenance"] == "orchestrator_commit_outcome"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["whatsapp", "whatsapp_cloud"])
async def test_whatsapp_confirmation_email_uses_booking_email_and_source(
    monkeypatch, channel
):
    field_defs = [
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone", "required": True},
        {"name": "date", "type": "date", "required": True},
        {"name": "time", "type": "time", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.llm",
        SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    content='{"response_text":"Ready to confirm.","extracted_fields":{},"invalid_fields":{}}'
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    async def _booking_gate(_db, _org, _org_config, chat_session, *_args, **_kwargs):
        chat_session.session_status = "CONFIRMED"
        chat_session.collected_data["booking_id"] = "booking-whatsapp-123"
        return ("Booking confirmed!", "BOOKING_CONFIRMED_CARD", None)

    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.message_processor._handle_booking_flow",
        AsyncMock(side_effect=_booking_gate),
    )
    send_confirmation = AsyncMock()
    monkeypatch.setattr("app.core.mail.send_confirmation_emails", send_confirmation)

    session = _chat_session(session_id=f"session-{channel}")
    session.channel = channel
    await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=session,
        message_text="confirm booking",
        background_tasks=None,
    )

    assert send_confirmation.await_count == 1
    args = send_confirmation.await_args.args
    assert args[0] == "customer@example.com"
    assert args[2]["booking_id"] == "booking-whatsapp-123"
    assert args[2]["source_channel"] == "WhatsApp"


@pytest.mark.asyncio
async def test_idempotent_replay_prevents_duplicate_otp_and_confirmation_side_effects(monkeypatch):
    field_defs = [
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone", "required": True},
        {"name": "date", "type": "date", "required": True},
        {"name": "time", "type": "time", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.llm",
        SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    content='{"response_text":"Proceeding.","extracted_fields":{},"invalid_fields":{}}'
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    async def _booking_gate(_db, _org, _org_config, chat_session, *_args, **_kwargs):
        chat_session.session_status = "CONFIRMED"
        chat_session.collected_data["booking_id"] = "booking-555"
        return ("Booking confirmed!", "BOOKING_CONFIRMED_CARD", None)

    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.message_processor._handle_booking_flow",
        AsyncMock(side_effect=_booking_gate),
    )
    generate_otp = AsyncMock(return_value="123456")
    verify_otp = AsyncMock(return_value=False)
    send_confirmation = AsyncMock()
    monkeypatch.setattr("app.crud.otp.generate_otp", generate_otp)
    monkeypatch.setattr("app.crud.otp.verify_otp", verify_otp)
    monkeypatch.setattr("app.core.mail.send_confirmation_emails", send_confirmation)

    replay_session = _chat_session(session_id="session-replay")

    await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=replay_session,
        message_text="confirm booking",
        background_tasks=None,
    )
    await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=replay_session,
        message_text="confirm booking",
        background_tasks=None,
    )

    assert generate_otp.await_count == 0
    assert verify_otp.await_count == 0
    assert send_confirmation.await_count == 1


@pytest.mark.asyncio
async def test_graph_path_persists_transcript_messages_for_dashboard_inbox(monkeypatch):
    field_defs = [
        {"name": "email", "type": "email", "required": True},
        {"name": "phone", "type": "phone", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    monkeypatch.setattr(
        "app.engine.agent.graph.llm",
        SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=AIMessage(
                    content='{"response_text":"Thanks, please share your phone number.","extracted_fields":{},"invalid_fields":{}}'
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )
    save_message = AsyncMock()
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.save_message", save_message)

    db = SimpleNamespace(add=object(), commit=object())
    await process_message_with_graph(
        db=db,
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=_chat_session(session_id="session-transcript"),
        message_text="hello",
        background_tasks=None,
    )

    assert save_message.await_count == 2
    assert save_message.await_args_list[0].args[2] == "user"
    assert save_message.await_args_list[1].args[2] == "agent"


def test_phone_email_runtime_contact_requirements_respect_required_fields_contract() -> None:
    follow_ups = _enforce_runtime_contact_requirements(
        [],
        {"email": "customer@example.com"},
        {"required_fields": ["date", "time"]},
    )

    assert follow_ups == []


@pytest.mark.asyncio
async def test_no_slot_date_reset_reopens_date_picker_with_guided_fallback_contract(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="No slots available for that date.")],
            "collected_fields": {
                "date": "2026-05-20",
                "time": "14:00:00",
                "selected_slot_id": "virtual|org|2026-05-20T14:00:00Z",
            },
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "slot_unavailable": True,
            "fallback_reason": "no_slots_available_for_date",
            "fallback_next_step": "pick_another_date",
            "refreshed_slots": [],
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock())
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )

    chat_session = ChatSessionState(
        session_id="session-slot-reset",
        org_id="00000000-0000-0000-0000-000000000001",
        current_intent="CONFIRM_BOOKING",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "date", "type": "date", "label": "Date", "required": True, "order": 0},
                {"name": "time", "type": "time", "label": "Time", "required": True, "order": 1},
            ],
            "date": "2026-05-20",
            "time": "14:00:00",
            "selected_slot_id": "virtual|org|2026-05-20T14:00:00Z",
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

    assert payload.widget_directive == "REQUIRE_DATE_PICKER"
    assert payload.field_meta["directive_retry"] == "REQUIRE_DATE_PICKER"
    assert payload.field_meta["fallback_reason"] == "no_slots_available_for_date"
    assert payload.field_meta["fallback_next_step"] == "pick_another_date"
    assert payload.field_meta["follow_up_fields"] == ["date"]
    assert "time" not in chat_session.collected_data
    assert "selected_slot_id" not in chat_session.collected_data

@pytest.mark.asyncio
async def test_edit_details_request_does_not_loop_back_to_confirmation_summary(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Sure, what would you like to change?")],
            "collected_fields": {},
            "follow_up_fields": [],
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
        session_id="session-edit-loop",
        org_id="00000000-0000-0000-0000-000000000001",
        current_intent="CONFIRM_BOOKING",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "name", "type": "text", "label": "Full Name", "required": True, "order": 0},
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
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=chat_session,
        message_text="I need to change some details.",
        background_tasks=None,
    )

    assert payload.widget_directive == "TEXT_INPUT"
    assert "what you'd like to change" in payload.message.lower()
    assert payload.field_meta["name"] == "edit_details"


@pytest.mark.asyncio
async def test_manual_time_text_without_slot_selection_reopens_time_slot_grid(monkeypatch):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="Noted.")],
            "collected_fields": {"time": "11:00 AM"},
            "follow_up_fields": [],
            "captured_deltas": {"time": "11:00 AM"},
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
        session_id="session-manual-time",
        org_id="00000000-0000-0000-0000-000000000001",
        current_intent="START_BOOKING",
        collected_data={
            "field_definitions_snapshot": [
                {"name": "date", "type": "date", "label": "Date", "required": True, "order": 0},
                {"name": "time", "type": "time", "label": "Time", "required": True, "order": 1},
            ],
            "date": "2026-05-20",
            "user_timezone": "Asia/Kolkata",
        },
        missing_fields=["time"],
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001")),
        org_config=SimpleNamespace(require_otp=False),
        chat_session=chat_session,
        message_text="11:00 AM",
        background_tasks=None,
    )

    assert payload.widget_directive == "REQUIRE_TIME_SLOT_GRID"
    assert chat_session.missing_fields[0] == "time"
    assert "time" not in chat_session.collected_data
