"""Decline decisions come from the LLM's verdict, never from word lists.

Regression coverage for a production transcript in which a Hindi-speaking
customer was refused five times in one conversation:
  - "Nam humra mahip hai" (a name) declined as off-topic,
  - "Name is mahip" declined,
  - "But i still want to book" declined (latched, never re-engaged),
  - "No worries dear" (filler) declined,
  - "No. Is 7777723415" declined and its phone number lost.

The routing rule under test: user_intent == decline_booking (and only that)
starts a decline; any other decisive verdict clears a latched decline; an
absent verdict (LLM failure) changes nothing. Message wording — in any
language — never decides. These tests mock the graph verdict and assert the
wiring; the model emitting the right verdicts is covered by prompt rule 9c
plus live verification, not here.
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.engine import decline_flow
from app.engine.langgraph_orchestrator import process_message_with_graph
from app.schemas.agent import ChatSessionState

ORG_ID = "00000000-0000-0000-0000-000000000001"
FAREWELL = "feel free to reach out"


def _session(**overrides):
    base = dict(
        session_id="sess-llm-decline",
        org_id=ORG_ID,
        channel="web",
        current_intent="PROVIDE_DATA",
        collected_data={
            "name": "Test User",
            "field_definitions_snapshot": [
                {"name": "name", "type": "text", "label": "Name",
                 "required": True, "order": 0},
                {"name": "phone", "type": "phone", "label": "Phone",
                 "required": True, "order": 1},
            ],
        },
        missing_fields=["phone"],
    )
    base.update(overrides)
    return ChatSessionState(**base)


def _graph(*, intent, response_text="Understood.", collected_fields=None,
           knowledge_answer=False):
    payload = {
        "response_text": response_text,
        "extracted_fields": {},
        "user_intent": intent,
    }
    result = {"messages": [AIMessage(content=json.dumps(payload))]}
    if collected_fields is not None:
        result["collected_fields"] = collected_fields
    if knowledge_answer:
        result["knowledge_answer"] = True
    return result


def _base_mocks(monkeypatch, graph_result=None, graph_error=None):
    field_defs = [
        {"name": "name", "type": "text", "required": True},
        {"name": "phone", "type": "phone", "required": True},
    ]
    monkeypatch.setattr(
        "app.engine.agent.graph.AgentContextBuilder.get_field_definitions",
        AsyncMock(return_value=field_defs),
    )
    if graph_error is not None:
        async def _boom(*a, **k):
            raise graph_error
        monkeypatch.setattr(
            "app.engine.langgraph_orchestrator.booking_agent.ainvoke", _boom
        )
    else:
        monkeypatch.setattr(
            "app.engine.langgraph_orchestrator.booking_agent.ainvoke",
            AsyncMock(return_value=graph_result),
        )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.update_chat_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_message",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.crud_chat.save_session_checkpoint",
        AsyncMock(),
    )
    mock_persist = AsyncMock()
    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator._persist_transcript_best_effort",
        mock_persist,
    )
    return mock_persist


def _call_kwargs(session, message_text):
    return dict(
        db=SimpleNamespace(),
        org=SimpleNamespace(id=uuid.UUID(ORG_ID)),
        org_config=SimpleNamespace(
            timezone="UTC", require_otp=False, require_phone_otp=False
        ),
        chat_session=session,
        message_text=message_text,
    )


@pytest.mark.asyncio
async def test_hindi_decline_verdict_starts_decline(monkeypatch):
    """No English refusal words in the input; the verdict alone decides."""
    _base_mocks(
        monkeypatch,
        _graph(intent="decline_booking", response_text="Theek hai."),
    )
    session = _session()
    action = await process_message_with_graph(
        **_call_kwargs(session, "nahi book karna")
    )
    assert FAREWELL in action.message.lower()
    assert decline_flow.is_declined(session) is True


@pytest.mark.asyncio
async def test_reengage_clears_latch_on_proceed_verdict(monkeypatch):
    """The transcript's worst turn: an explicit request to book, refused."""
    _base_mocks(
        monkeypatch,
        _graph(intent="proceed", response_text="Great, let's continue."),
    )
    session = _session(
        collected_data={
            **_session().collected_data,
            decline_flow.BOOKING_DECLINED_KEY: True,
        }
    )
    assert decline_flow.is_declined(session) is True
    action = await process_message_with_graph(
        **_call_kwargs(session, "But i still want to book")
    )
    assert decline_flow.is_declined(session) is False
    assert FAREWELL not in action.message.lower()


@pytest.mark.asyncio
async def test_llm_failure_never_starts_a_decline(monkeypatch):
    """Fail open toward serving the customer, never refuse on unknown."""
    _base_mocks(monkeypatch, graph_error=Exception("LLM down"))
    session = _session()
    action = await process_message_with_graph(
        **_call_kwargs(session, "I don't want to book")
    )
    assert decline_flow.is_declined(session) is False
    assert FAREWELL not in action.message.lower()


@pytest.mark.asyncio
async def test_latch_survives_llm_failure(monkeypatch):
    """Conservative the other way: an outage must not resurrect
    interrogation on a genuinely declined session."""
    _base_mocks(monkeypatch, graph_error=Exception("LLM down"))
    session = _session(
        collected_data={
            **_session().collected_data,
            decline_flow.BOOKING_DECLINED_KEY: True,
        }
    )
    await process_message_with_graph(**_call_kwargs(session, "hello?"))
    assert decline_flow.is_declined(session) is True


@pytest.mark.asyncio
async def test_multi_intent_phone_survives_leading_no(monkeypatch):
    """Hinglish multi-intent: refusal-shaped prefix plus a real value."""
    _base_mocks(
        monkeypatch,
        _graph(
            intent="proceed",
            response_text="Noted.",
            collected_fields={"phone": "7777723415"},
        ),
    )
    session = _session()
    action = await process_message_with_graph(
        **_call_kwargs(session, "Nahi, 7777723415")
    )
    assert FAREWELL not in action.message.lower()
    assert decline_flow.is_declined(session) is False
    stored = str((session.collected_data or {}).get("phone") or "")
    assert "7777723415" in "".join(ch for ch in stored if ch.isdigit())


@pytest.mark.asyncio
async def test_hindi_name_is_extracted_not_refused(monkeypatch):
    """A name in Hindi reaches extraction instead of dying as off-topic."""
    _base_mocks(
        monkeypatch,
        _graph(
            intent="proceed",
            response_text="Dhanyavaad!",
            collected_fields={"name": "Mahip"},
        ),
    )
    session = _session(
        collected_data={
            "field_definitions_snapshot": [
                {"name": "name", "type": "text", "label": "Name",
                 "required": True, "order": 0},
            ],
        },
        missing_fields=["name"],
        current_intent="START_BOOKING",
    )
    action = await process_message_with_graph(
        **_call_kwargs(session, "Nam humra mahip hai")
    )
    assert FAREWELL not in action.message.lower()
    assert decline_flow.is_declined(session) is False
    assert "mahip" in str((session.collected_data or {}).get("name") or "").lower()


@pytest.mark.asyncio
async def test_filler_is_neither_decline_nor_question(monkeypatch):
    """Conversational filler continues the flow; it is not a refusal and
    not a knowledge question."""
    _base_mocks(
        monkeypatch,
        _graph(intent="clarify", response_text="No worries at all."),
    )
    session = _session()
    action = await process_message_with_graph(
        **_call_kwargs(session, "No worries dear")
    )
    assert FAREWELL not in action.message.lower()
    assert decline_flow.is_declined(session) is False


@pytest.mark.asyncio
async def test_resist_alone_never_farewells(monkeypatch):
    """'resist' is ambiguous with field-level pushback, so only the explicit
    decline_booking verdict may start a farewell."""
    _base_mocks(
        monkeypatch,
        _graph(intent="resist", response_text="Understood."),
    )
    session = _session()
    action = await process_message_with_graph(
        **_call_kwargs(session, "No email sorry")
    )
    assert FAREWELL not in action.message.lower()
    assert decline_flow.is_declined(session) is False


@pytest.mark.asyncio
async def test_mixed_decline_plus_question_latches_but_answers(monkeypatch):
    """Decline verdict alongside a real knowledge answer: record the state
    so nothing interrogates further, but let the answer through instead of
    swallowing the question."""
    _base_mocks(
        monkeypatch,
        _graph(
            intent="decline_booking",
            response_text="We are open 9 to 5 on weekdays.",
            knowledge_answer=True,
        ),
    )
    session = _session()
    action = await process_message_with_graph(
        **_call_kwargs(session, "Rehndo, timings kya hain?")
    )
    assert decline_flow.is_declined(session) is True
    assert FAREWELL not in action.message.lower()
    assert "9 to 5" in action.message
