"""The persisted context window must carry BOTH sides of the conversation.

Production failure: every stored window held human messages only — the
graph's own context_window output never contains its reply, and the
orchestrator persisted it verbatim. The model never saw its own prior
questions, so after any detour it re-asked collected fields and the summary
carried chatter alone. A second failure: an error turn persisted an empty
window/summary, wiping context that was fine a turn earlier.
"""
from langchain_core.messages import AIMessage, HumanMessage

from app.engine.langgraph_orchestrator import _graph_messages_to_window


def test_window_keeps_both_roles_in_order() -> None:
    messages = [
        HumanMessage(content="I am Lulu"),
        AIMessage(content="Thanks Lulu! Could you please share your email address?"),
        HumanMessage(content="lulu@example.com"),
        AIMessage(content="Thanks! Could you please share your phone number?"),
    ]
    window = _graph_messages_to_window(messages)
    assert [item["role"] for item in window] == ["human", "ai", "human", "ai"]
    assert window[1]["content"] == (
        "Thanks Lulu! Could you please share your email address?"
    )


def test_ai_json_blob_is_reduced_to_visible_text() -> None:
    """Raw blobs would leak contract JSON into future prompts."""
    raw = (
        '{"response_text": "Got it! What date works for you?", '
        '"extracted_fields": {"phone": "+911234567890"}, "user_intent": "proceed"}'
    )
    window = _graph_messages_to_window(
        [HumanMessage(content="98765 43210"), AIMessage(content=raw)]
    )
    assert window[1] == {"role": "ai", "content": "Got it! What date works for you?"}
    assert "extracted_fields" not in window[1]["content"]


def test_window_is_capped_to_six_with_latest_turns() -> None:
    messages = []
    for index in range(5):
        messages.append(HumanMessage(content=f"chatter {index}"))
        messages.append(AIMessage(content=f"reply {index}"))
    window = _graph_messages_to_window(messages)
    assert len(window) == 6
    assert window[-2:] == [
        {"role": "human", "content": "chatter 4"},
        {"role": "ai", "content": "reply 4"},
    ]


def test_empty_or_junk_input_yields_empty_window() -> None:
    assert _graph_messages_to_window(None) == []
    assert _graph_messages_to_window([]) == []
    assert _graph_messages_to_window("not-a-list") == []
    assert _graph_messages_to_window(
        [HumanMessage(content="   "), AIMessage(content="")]
    ) == []
