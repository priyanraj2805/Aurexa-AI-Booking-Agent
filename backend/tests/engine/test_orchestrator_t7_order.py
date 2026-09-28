import pytest
from app.engine.langgraph_orchestrator import _enforce_runtime_contact_requirements

def test_enforce_returns_email_phone_in_order():
    org_config = {
        "field_definitions": [
            {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
            {"name": "phone", "type": "phone", "label": "Phone", "required": True, "order": 1},
        ]
    }
    result = _enforce_runtime_contact_requirements([], {}, org_config)
    assert result == ["email", "phone"]

def test_enforce_does_not_duplicate_when_already_present():
    org_config = {
        "field_definitions": [
            {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
        ]
    }
    result = _enforce_runtime_contact_requirements(["email"], {}, org_config)
    assert result == ["email"]

def test_enforce_respects_collected():
    org_config = {
        "field_definitions": [
            {"name": "email", "type": "email", "label": "Email", "required": True, "order": 0},
            {"name": "phone", "type": "phone", "label": "Phone", "required": True, "order": 1},
        ]
    }
    result = _enforce_runtime_contact_requirements([], {"email": "a@b.com"}, org_config)
    assert result == ["phone"]
