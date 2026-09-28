import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState


def _dup_booking():
    return SimpleNamespace(
        id="booking-1",
        customer_name="william",
        customer_email="wilia@gmail.com",
        customer_phone="+916350063338",
    )


def _full_session(**overrides):
    data = {
        "field_definitions_snapshot": [
            {"name": "name", "type": "text", "label": "Name", "required": True, "order": 0},
            {"name": "email", "type": "email", "label": "Email", "required": True, "order": 1},
            {"name": "phone", "type": "phone", "label": "Phone", "required": True, "order": 2},
            {"name": "date", "type": "date", "label": "Date", "required": True, "order": 3},
            {"name": "time", "type": "time", "label": "Time", "required": True, "order": 4},
        ],
        "name": "william",
        "email": "wilia@gmail.com",
        "phone": "+916350063338",
        "date": "2026-10-05",
        "time": "09:00",
    }
    params = {
        "session_id": "session-nonbooking",
        "org_id": "00000000-0000-0000-0000-000000000001",
        "channel": "whatsapp",
        "current_intent": "START_BOOKING",
        "collected_data": data,
        "missing_fields": [],
    }
    params.update(overrides)
    return ChatSessionState(**params)


def _mock_common(monkeypatch, llm_text, llm_intent="START_BOOKING"):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content=llm_text)],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": llm_intent,
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock()
    )
    monkeypatch.setattr(
        "app.engine.cancel_flow._find_upcoming_booking",
        AsyncMock(return_value=(_dup_booking(), None)),
    )


@pytest.mark.asyncio
async def test_greeting_gets_area_guidance_not_duplicate_lecture(monkeypatch):
    _mock_common(monkeypatch, "Heyyy! Great to see you here.")
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(),
        message_text="Heyyy broo",
        background_tasks=None,
    )

    assert "already have an appointment" not in payload.message
    assert payload.widget_directive == "TEXT_INPUT"
    assert payload.field_meta.get("duplicate_booking") is not True


@pytest.mark.asyncio
async def test_knowledge_question_preserves_answer_over_duplicate_guard(monkeypatch):
    _mock_common(monkeypatch, "DotStark is an IT consultancy.", llm_intent="ASK_QUESTION")
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(),
        message_text="Tell me about dotstark",
        background_tasks=None,
    )

    assert "IT consultancy" in payload.message
    assert "already have an appointment" not in payload.message
    assert payload.widget_directive == "TEXT_INPUT"


@pytest.mark.asyncio
async def test_out_of_scope_gets_area_guidance(monkeypatch):
    _mock_common(monkeypatch, "Blorp floop zzz.")
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(),
        message_text="blorp floop zzz",
        background_tasks=None,
    )

    assert "outside what I can help with" in payload.message
    assert "DotStark" in payload.message
    assert "anything else" in payload.message
    assert payload.widget_directive == "TEXT_INPUT"


@pytest.mark.asyncio
async def test_code_embedded_in_text_is_verified(monkeypatch):
    session = _full_session()
    session.collected_data["_active_path_effects"] = {"otp_sent_keys": ["otp_phone:session-nonbooking:+916350063338"]}

    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="ok")],
            "collected_fields": {},
            "follow_up_fields": ["name"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.crud.otp.verify_otp", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.get_chat_session", AsyncMock(return_value=session)
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock()
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=True, timezone="UTC"),
        chat_session=session,
        message_text="my code is 123456",
        background_tasks=None,
    )

    assert session.phone_otp_verified is True
    assert "incorrect or has expired" not in payload.message


def _mock_graph(monkeypatch, llm_text, llm_intent="START_BOOKING"):
    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content=llm_text)],
            "collected_fields": {},
            "follow_up_fields": [],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": llm_intent,
        }

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock()
    )


@pytest.mark.asyncio
async def test_bare_confirm_reaches_booking_gate_despite_stale_chitchat_intent(monkeypatch):
    """Bare 'confirm' must resolve to PROCESS_BOOKING even when a stale
    CHITCHAT intent lingers on the session (the stuck-loop regression)."""
    _mock_graph(monkeypatch, "confirm?", llm_intent="START_BOOKING")
    gate_calls = []

    async def _fake_gate(*_args, **_kwargs):
        gate_calls.append(True)
        return ("gate reached", "REQUIRE_OTP_MODAL", {})

    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.message_processor._handle_booking_flow",
        _fake_gate,
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(current_intent="CHITCHAT"),
        message_text="confirm",
        background_tasks=None,
    )

    assert gate_calls == [True]
    assert payload.message == "gate reached"


@pytest.mark.asyncio
async def test_bare_ok_reaches_booking_gate(monkeypatch):
    _mock_graph(monkeypatch, "ok?", llm_intent="START_BOOKING")
    gate_calls = []

    async def _fake_gate(*_args, **_kwargs):
        gate_calls.append(True)
        return ("gate reached", "REQUIRE_OTP_MODAL", {})

    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.message_processor._handle_booking_flow",
        _fake_gate,
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(),
        message_text="ok",
        background_tasks=None,
    )

    assert gate_calls == [True]
    assert payload.message == "gate reached"


def test_pure_confirm_never_matches_cancel_sentences():
    from app.engine.time_flex import is_pure_confirm_message

    assert is_pure_confirm_message("confirm") is True
    assert is_pure_confirm_message("Confirm!") is True
    assert is_pure_confirm_message("cancel my booking") is False
    assert is_pure_confirm_message("reschedule my booking") is False
    assert is_pure_confirm_message("Human") is False


@pytest.mark.asyncio
async def test_post_booking_confirm_stable_without_llm(monkeypatch):
    from app.engine import langgraph_orchestrator as _lo

    async def _boom(*_a, **_k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(_lo, "_llm_post_booking_analysis", _boom)
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    session = _full_session(session_status="CONFIRMED")
    session.collected_data["date"] = "2026-09-28"
    session.collected_data["time"] = "15:00"
    first = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=session,
        message_text="confirm",
        background_tasks=None,
    )
    second = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=session,
        message_text="confirm",
        background_tasks=None,
    )

    expected = "Your booking is already confirmed for September 28 at 3 PM. Let us know if you need anything else."
    assert first.message == expected
    assert second.message == expected
    assert first.widget_directive == "TEXT_INPUT"


@pytest.mark.asyncio
async def test_post_booking_misclassified_human_stays_stable(monkeypatch):
    from app.engine import langgraph_orchestrator as _lo

    monkeypatch.setattr(
        _lo,
        "_llm_post_booking_analysis",
        AsyncMock(
            return_value={
                "classification": "CONFIRM",
                "action": "NONE",
                "confidence": 0.9,
                "reply": "Thanks for confirming! Your appointment is locked in forever!",
            }
        ),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    session = _full_session(session_status="CONFIRMED")
    session.collected_data["date"] = "2026-09-28"
    session.collected_data["time"] = "15:00"
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=session,
        message_text="Human",
        background_tasks=None,
    )

    assert "locked in forever" not in payload.message
    assert "already confirmed for September 28 at 3 PM" in payload.message
    assert payload.widget_directive == "TEXT_INPUT"


@pytest.mark.asyncio
async def test_post_booking_question_still_answered(monkeypatch):
    from app.engine import langgraph_orchestrator as _lo

    monkeypatch.setattr(
        _lo,
        "_llm_post_booking_analysis",
        AsyncMock(
            return_value={
                "classification": "INFO_QUESTION",
                "action": "NONE",
                "confidence": 0.9,
                "reply": "You can cancel by saying 'cancel my booking'.",
            }
        ),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(session_status="CONFIRMED"),
        message_text="how do I cancel?",
        background_tasks=None,
    )

    assert "cancel by saying" in payload.message
    assert payload.widget_directive == "TEXT_INPUT"


@pytest.mark.asyncio
async def test_post_booking_cancel_still_routes(monkeypatch):
    from app.engine import langgraph_orchestrator as _lo
    from app.schemas.agent import ActionPayload

    monkeypatch.setattr(
        _lo,
        "_llm_post_booking_analysis",
        AsyncMock(
            return_value={
                "classification": "ACTION_REQUEST",
                "action": "CANCEL",
                "confidence": 0.95,
                "reply": "",
            }
        ),
    )
    started = {}

    async def _fake_start(db, chat_session, message_text):
        started["called"] = True
        return ActionPayload(message="cancel flow started", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(_lo.cancel_flow, "start_cancel_flow", _fake_start)
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=False, timezone="UTC"),
        chat_session=_full_session(session_status="CONFIRMED"),
        message_text="cancel my booking",
        background_tasks=None,
    )

    assert started.get("called") is True
    assert payload.message == "cancel flow started"


@pytest.mark.asyncio
async def test_legacy_post_booking_confirm_stable_without_llm(db_session, monkeypatch):
    from sqlalchemy.future import select as _sel

    from app.crud import chat as crud_chat
    from app.engine import message_processor
    from app.models.config import OrgConfig
    from app.models.organization import Organization

    org = Organization(name="Postbook Org", api_key=f"ds_{uuid.uuid4().hex}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    db_session.add(
        OrgConfig(
            org_id=org.id,
            required_fields=["name", "email", "phone", "date", "time"],
            require_otp=False,
            require_phone_otp=False,
            timezone="UTC",
        )
    )
    await db_session.commit()
    created = await crud_chat.create_chat_session(
        db_session, str(org.id), channel="whatsapp", channel_user_id="wa_9100000071"
    )
    sess = await crud_chat.get_chat_session(db_session, str(created.id))
    assert sess is not None
    sess.session_status = "CONFIRMED"
    sess.current_intent = "CONFIRM_BOOKING"
    sess.missing_fields = []
    sess.collected_data = {
        "name": "william",
        "email": "wilia@gmail.com",
        "phone": "+916350063338",
        "date": "2026-09-28",
        "time": "15:00",
    }
    await crud_chat.update_chat_session(db_session, str(created.id), sess)
    sess = await crud_chat.get_chat_session(db_session, str(created.id))
    assert sess is not None

    async def _boom(*_a, **_k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(message_processor, "_llm_post_booking_self_check", _boom)
    cfg = (
        await db_session.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
    ).scalars().first()
    payload = await message_processor._handle_post_booking(
        db_session, org, cfg, sess, "confirm"
    )

    assert payload.message == (
        "Your booking is already confirmed for September 28 at 3 PM. "
        "Let us know if you need anything else."
    )
    assert payload.widget_directive == "TEXT_INPUT"


@pytest.mark.asyncio
async def test_verify_race_recovers_when_session_already_verified(monkeypatch):
    session = _full_session()
    session.collected_data["_active_path_effects"] = {"otp_sent_keys": ["otp_phone:session-nonbooking:+916350063338"]}

    async def _ainvoke(_inputs, config=None):
        return {
            "messages": [AIMessage(content="ok")],
            "collected_fields": {},
            "follow_up_fields": ["name"],
            "captured_deltas": {},
            "summary": "",
            "context_window": [],
            "current_intent": "START_BOOKING",
        }

    verified = _full_session()
    verified.phone_otp_verified = True
    verified.collected_data["_active_path_effects"] = {}

    monkeypatch.setattr("app.engine.langgraph_orchestrator.booking_agent.ainvoke", _ainvoke)
    monkeypatch.setattr("app.crud.otp.verify_otp", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.get_chat_session", AsyncMock(return_value=verified)
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session", AsyncMock()
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint", AsyncMock()
    )

    payload = await process_message_with_graph(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), name="DotStark"),
        org_config=SimpleNamespace(require_otp=False, require_phone_otp=True, timezone="UTC"),
        chat_session=session,
        message_text="123456",
        background_tasks=None,
    )

    assert "incorrect or has expired" not in payload.message
