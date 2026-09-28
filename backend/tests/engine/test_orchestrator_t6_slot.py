import pytest
from types import SimpleNamespace
from app.engine.langgraph_orchestrator import _slot_policy_kwargs

def test_slot_policy_kwargs_returns_org_values():
    org_cfg = SimpleNamespace(allow_same_day_booking=True, slot_cutoff_minutes=10)
    result = _slot_policy_kwargs(org_cfg)
    assert result == {"allow_same_day_booking": True, "slot_cutoff_minutes": 10}

def test_slot_policy_kwargs_defaults():
    org_cfg = SimpleNamespace()
    result = _slot_policy_kwargs(org_cfg)
    assert result == {"allow_same_day_booking": False, "slot_cutoff_minutes": 5}

def test_no_hardcoded_slot_policy_outside_helper():
    import pathlib
    # When pytest runs from backend dir, path is app/..., when from root, it's backend/app/...
    for p in ["app/engine/langgraph_orchestrator.py", "backend/app/engine/langgraph_orchestrator.py"]:
        try:
            text = pathlib.Path(p).read_text()
            break
        except Exception:
            continue
    else:
        text = pathlib.Path(__file__).parents[2].joinpath("app/engine/langgraph_orchestrator.py").read_text()
    # Count occurrences of the literal outside helper - should be 0 after fix
    # Helper itself contains "False" and "5" as getattr defaults, but not as keyword literal
    # So check that "allow_same_day_booking=False" as keyword does not appear
    assert text.count("allow_same_day_booking=False") == 0
    assert text.count("slot_cutoff_minutes=5") == 0
    # But helper should exist
    assert "_slot_policy_kwargs" in text
