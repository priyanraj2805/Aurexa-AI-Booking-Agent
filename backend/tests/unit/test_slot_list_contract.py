"""Slot-list requests travel as a MODEL flag, never keyword code.

Whether the customer asked for available times/slots ("tell me timings",
"timings batao", any paraphrase in any language) is the model's judgment,
expressed as structured ``wants_slot_list`` in its JSON output. The renderer
uses the flag to decide whether computed inventory replaces the reply.
"""
from app.engine.agent.prompts import build_system_prompt
from app.engine.orchestrator import LLMOrchestrator
from app.engine.prompts import PromptGenerator


def test_graph_prompt_contracts_slot_flag() -> None:
    prompt = build_system_prompt(
        {"business_name": "Test Clinic", "timezone": "UTC"},
        [{"name": "time", "type": "time", "required": True}],
    )
    assert "wants_slot_list" in prompt


def test_unified_prompt_contracts_slot_flag() -> None:
    prompt = PromptGenerator.build_unified_turn_prompt(
        "tell me timings",
        ["time"],
        {},
        "PROVIDE_DATA",
    )
    assert "wants_slot_list" in prompt


def test_unified_validator_normalises_slot_flag() -> None:
    ok = LLMOrchestrator._validate_unified_response(
        {
            "intent": "PROVIDE_DATA",
            "extracted_entities": {},
            "response_message": "Sure.",
            "wants_slot_list": True,
        }
    )
    assert ok["wants_slot_list"] is True
    missing = LLMOrchestrator._validate_unified_response(
        {
            "intent": "PROVIDE_DATA",
            "extracted_entities": {},
            "response_message": "Sure.",
        }
    )
    assert missing["wants_slot_list"] is False
