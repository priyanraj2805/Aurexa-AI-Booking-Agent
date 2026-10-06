import pytest
from app.engine.prompts import PromptGenerator

def test_intent_prompt_generation():
    state = {"collected_data": {"name": "Test"}}
    message = "I want to book an appointment"
    
    prompt = PromptGenerator.build_intent_prompt(message, state)
    
    assert "START_BOOKING" in prompt
    assert "I want to book an appointment" in prompt
    assert "You MUST output EXACTLY ONE" in prompt

def test_unified_turn_prompt_generation():
    missing = ["time", "phone"]
    collected = {"name": "Test"}
    message = "Put me down for 5 PM my number is 555-1234"
    
    prompt = PromptGenerator.build_unified_turn_prompt(
        message, missing, collected, current_intent="PROVIDE_DATA"
    )
    
    assert "time, phone" in prompt
    assert "Put me down for 5 PM my number is 555-1234" in prompt
    assert "Output ONLY valid JSON" in prompt


def test_unified_turn_prompt_bans_ai_style():
    prompt = PromptGenerator.build_unified_turn_prompt(
        "hi", ["name"], {}, current_intent="START_BOOKING"
    )
    assert "em dash" in prompt.lower()
    assert "As an AI" in prompt


def test_graph_system_prompt_bans_ai_style():
    from app.engine.agent.prompts import build_system_prompt

    prompt = build_system_prompt(
        {"business_name": "Test Biz", "persona": {}},
        [{"name": "name", "type": "text", "required": True}],
    )
    assert "em dash" in prompt.lower()
    assert "As an AI" in prompt
    assert "REQUIRED FIELDS" in prompt


def test_first_turn_greeting_rule_in_both_prompts():
    """The first reply must always open with a greeting, whatever the
    first message contains — instructed to the MODEL, never keyword code."""
    from app.engine.agent.prompts import build_system_prompt

    graph_first = build_system_prompt(
        {"business_name": "Test Biz", "persona": {}},
        [{"name": "name", "type": "text", "required": True}],
        is_first_turn=True,
    )
    assert "FIRST MESSAGE OF THE CONVERSATION" in graph_first
    graph_later = build_system_prompt(
        {"business_name": "Test Biz", "persona": {}},
        [{"name": "name", "type": "text", "required": True}],
        is_first_turn=False,
    )
    assert "FIRST MESSAGE OF THE CONVERSATION" not in graph_later

    unified_first = PromptGenerator.build_unified_turn_prompt(
        "what are your hours?", ["name"], {}, "START_BOOKING",
        is_first_turn=True,
    )
    assert "FIRST MESSAGE OF THE CONVERSATION" in unified_first
    unified_later = PromptGenerator.build_unified_turn_prompt(
        "what are your hours?", ["email"], {"name": "X"}, "PROVIDE_DATA",
    )
    assert "FIRST MESSAGE OF THE CONVERSATION" not in unified_later
