from app.core.verification_gate import evaluate_milestone_closure


def test_gaps_found_blocks_without_override():
    decision = evaluate_milestone_closure("gaps_found", proceed_anyway=False)
    assert decision.can_close is False
    assert decision.requires_override is True
    assert decision.must_record_known_gaps is False


def test_gaps_found_allows_with_explicit_override():
    decision = evaluate_milestone_closure("gaps_found", proceed_anyway=True)
    assert decision.can_close is True
    assert decision.requires_override is True
    assert decision.must_record_known_gaps is True


def test_passed_allows_closure_without_override():
    decision = evaluate_milestone_closure("passed", proceed_anyway=False)
    assert decision.can_close is True
    assert decision.requires_override is False
    assert decision.must_record_known_gaps is False

