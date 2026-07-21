from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage


@pytest.mark.asyncio
async def test_call_model_compacts_messages_to_bounded_window_and_carries_summary(monkeypatch):
    from app.engine.agent import graph

    class _FakeContextBuilder:
        def __init__(self, *_args, **_kwargs):
            pass

        async def get_field_definitions(self):
            return [{"name": "name", "required": True}]

    llm_messages = {}

    class _FakeLLM:
        async def ainvoke(self, messages):
            llm_messages["messages"] = messages
            return AIMessage(content='{"extracted_fields": {}}')

    monkeypatch.setattr(graph, "AgentContextBuilder", _FakeContextBuilder)
    monkeypatch.setattr(graph, "llm", _FakeLLM())
    monkeypatch.setattr(graph, "MAX_ACTIVE_CONTEXT_MESSAGES", 4)

    state = {
        "messages": [HumanMessage(content=f"user-{idx}") for idx in range(10)],
        "summary": "prior-summary",
        "collected_fields": {},
        "optional_skips": [],
    }
    result = await graph.call_model(state, {"configurable": {"db": object(), "org_config": {}}})

    assert len(llm_messages["messages"]) == 5  # system + bounded active window
    assert "prior-summary" in result["summary"]
    assert "user-0" in result["summary"]
    assert len(result["context_window"]) == 4
    assert result["context_window"][-1]["content"] == "user-9"


@pytest.mark.asyncio
async def test_runtime_orchestrator_binds_graph_inputs_to_bounded_context_window(monkeypatch):
    from app.engine import langgraph_orchestrator as orchestrator

    agent_calls = []

    class _FakeAgent:
        async def ainvoke(self, inputs, config):
            agent_calls.append(inputs)
            return {
                "messages": [AIMessage(content="ok")],
                "collected_fields": {},
                "follow_up_fields": [],
                "captured_deltas": {},
                "merge_diagnostics": {},
                "summary": "rolled-summary",
                "context_window": [
                    {"role": "human", "content": "recent-1"},
                    {"role": "human", "content": "recent-2"},
                ],
            }

    monkeypatch.setattr(orchestrator, "booking_agent", _FakeAgent())
    monkeypatch.setattr(orchestrator, "MAX_RUNTIME_CONTEXT_MESSAGES", 3)
    monkeypatch.setattr(orchestrator.crud_chat, "update_chat_session", AsyncMock())
    monkeypatch.setattr(orchestrator.crud_chat, "save_session_checkpoint", AsyncMock())

    chat_session = SimpleNamespace(
        session_id="session-ctx",
        collected_data={
            "runtime_checkpoint": {
                "state": {
                    "summary": "older-summary",
                    "context_window": [
                        {"role": "human", "content": f"prior-{idx}"}
                        for idx in range(8)
                    ],
                }
            }
        },
        missing_fields=[],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
        otp_verified=False,
        pending_cancel=False,
    )

    await orchestrator.process_message_with_graph(
        db=object(),
        org=SimpleNamespace(id="org-1"),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="new-turn-1",
    )
    await orchestrator.process_message_with_graph(
        db=object(),
        org=SimpleNamespace(id="org-1"),
        org_config=SimpleNamespace(),
        chat_session=chat_session,
        message_text="new-turn-2",
    )

    assert len(agent_calls) == 2
    assert all(len(call["messages"]) <= 3 for call in agent_calls)


@pytest.mark.asyncio
async def test_call_model_tolerates_missing_config(monkeypatch):
    from app.engine.agent import graph

    class _FakeContextBuilder:
        def __init__(self, db, org_config):
            assert db is None
            assert org_config == {}

        async def get_field_definitions(self):
            return [{"name": "name", "required": True}]

    class _FakeLLM:
        async def ainvoke(self, _messages):
            return AIMessage(content='{"extracted_fields": {}}')

    monkeypatch.setattr(graph, "AgentContextBuilder", _FakeContextBuilder)
    monkeypatch.setattr(graph, "llm", _FakeLLM())

    state = {
        "messages": [HumanMessage(content="hello")],
        "summary": "",
        "collected_fields": {},
        "optional_skips": [],
    }

    result = await graph.call_model(state)

    assert result["messages"]


def test_normalize_llm_base_url_trims_chat_completions_suffix():
    from app.engine.agent import graph

    assert (
        graph._normalize_llm_base_url("https://api.groq.com/openai/v1/chat/completions")
        == "https://api.groq.com/openai/v1"
    )
    assert (
        graph._normalize_llm_base_url("https://api.groq.com/openai/v1")
        == "https://api.groq.com/openai/v1"
    )
