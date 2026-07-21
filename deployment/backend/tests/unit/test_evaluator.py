import pytest
from app.engine.evaluator import StateEvaluator

def test_calculate_missing_fields_empty_state():
    required = ["name", "email", "phone"]
    collected = {}
    
    missing = StateEvaluator.calculate_missing_fields(required, collected)
    
    assert len(missing) == 3
    assert "name" in missing
    assert "email" in missing
    assert "phone" in missing

def test_calculate_missing_fields_partial_state():
    required = ["name", "email", "phone"]
    collected = {
        "name": "John Doe",
        "email": None # Should be caught as missing
    }
    
    missing = StateEvaluator.calculate_missing_fields(required, collected)
    
    assert len(missing) == 2
    assert "email" in missing
    assert "phone" in missing
    assert "name" not in missing

def test_calculate_missing_fields_empty_strings():
    required = ["name", "email"]
    collected = {
        "name": "   ", # purely whitespace should fail
        "email": "" # purely empty should fail
    }
    
    missing = StateEvaluator.calculate_missing_fields(required, collected)
    
    assert len(missing) == 2
    assert "name" in missing
    assert "email" in missing

def test_determine_next_action_routing():
    # Test text routing
    action = StateEvaluator.determine_next_action(["name"], "PROVIDE_NAME")
    assert action["directive"] == "TEXT_INPUT"
    assert action["focus"] == "name"
    
    # Test specific frontend widget directives
    action = StateEvaluator.determine_next_action(["date", "time"], "PROVIDE_DATE")
    assert action["directive"] == "REQUIRE_DATE_PICKER"
    assert action["focus"] == "date"
    
    action = StateEvaluator.determine_next_action(["time"], "PROVIDE_TIME")
    assert action["directive"] == "REQUIRE_TIME_SLOT_GRID"
    assert action["focus"] == "time"
    
    # Test booking confirmation routing
    action = StateEvaluator.determine_next_action([], "CONFIRM_BOOKING")
    assert action["directive"] == "PROCESS_BOOKING"
    assert action["focus"] == "none"
