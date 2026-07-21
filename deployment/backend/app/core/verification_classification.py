def classify_gap_type(
    verification_status: str,
    in_summary: bool,
    in_traceability: bool,
    tests_green: bool,
) -> str:
    if verification_status == "gaps_found" and not tests_green:
        return "implementation_debt"
    if in_traceability and tests_green and (verification_status in ("passed", "missing")) and not in_summary:
        return "documentation_debt"
    if not in_traceability:
        return "process_debt"
    return "documentation_debt"
