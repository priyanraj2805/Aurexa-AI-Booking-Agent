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
