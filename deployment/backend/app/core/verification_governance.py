import re


def normalize_req_id(raw: str) -> str:
    text = raw.strip().upper()
    text = re.sub(r"[\s_]+", "-", text)
    match = re.match(r"^([A-Z]+)-?(\d+)$", text)
    if not match:
        return text
    prefix, number = match.groups()
    return f"{prefix}-{int(number):02d}"


def compute_requirement_status(
    verification_status: str | None,
    in_summary: bool,
    in_requirements: bool,
    orphaned: bool = False,
) -> str:
    if orphaned:
        return "orphaned"
    if not in_requirements:
        return "orphaned"
    if verification_status == "gaps_found":
        return "unsatisfied"
    if verification_status == "passed" and in_summary:
        return "satisfied"
    if verification_status == "passed" and not in_summary:
        return "partial"
    if verification_status in (None, "missing") and in_summary:
        return "partial"
    return "unsatisfied"
