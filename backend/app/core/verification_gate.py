from dataclasses import dataclass


@dataclass
class ClosureDecision:
    can_close: bool
    requires_override: bool
    must_record_known_gaps: bool


def evaluate_milestone_closure(audit_status: str, proceed_anyway: bool) -> ClosureDecision:
    if audit_status == "passed":
        return ClosureDecision(can_close=True, requires_override=False, must_record_known_gaps=False)

    if audit_status == "gaps_found":
        if proceed_anyway:
            return ClosureDecision(
                can_close=True,
                requires_override=True,
                must_record_known_gaps=True,
            )
        return ClosureDecision(
            can_close=False,
            requires_override=True,
            must_record_known_gaps=False,
        )

    return ClosureDecision(can_close=True, requires_override=False, must_record_known_gaps=False)
