"""Tests for admin-collected knowledge context (business profile + source
context) and its injection into agent prompts.

Covers:
- knowledge_profile formatters (profile block, source labels, sanitizer caps)
- system-prompt injection via build_system_prompt
- legacy persona-copy injection contract (shallow copy, no ORM mutation)
"""

from app.engine.knowledge_profile import (
    format_business_profile,
    format_source_context_line,
    get_business_profile,
    get_source_context,
    sanitize_source_context,
)
from app.engine.agent.prompts import build_system_prompt


# ─── Business profile extraction ─────────────────────────────────────────


def test_get_business_profile_none():
    assert get_business_profile(None) == {}
    assert get_business_profile("junk") == {}
    assert get_business_profile({}) == {}


def test_get_business_profile_present():
    ctx = {"business_profile": {"business_description": "dental clinic"}}
    assert get_business_profile(ctx)["business_description"] == "dental clinic"


def test_get_source_context_missing():
    assert get_source_context(None, "website") == {}
    assert get_source_context({}, "doc-1") == {}
    ctx = {"sources": {"doc-1": {"source_category": "Pricing"}}}
    assert get_source_context(ctx, "doc-1")["source_category"] == "Pricing"
    assert get_source_context(ctx, "other") == {}


# ─── Profile prompt block ────────────────────────────────────────────────


def test_format_business_profile_empty():
    assert format_business_profile(None) == ""
    assert format_business_profile({}) == ""
    assert format_business_profile({"business_profile": {}}) == ""


def test_format_business_profile_renders_fields():
    ctx = {
        "business_profile": {
            "business_name": "Smile Dental",
            "business_description": "Dental clinic in Pune",
            "key_policies": "Free first consult",
        }
    }
    block = format_business_profile(ctx)
    assert "ABOUT THE BUSINESS" in block
    assert "authoritative" in block
    assert "- Business name: Smile Dental" in block
    assert "- What the business does: Dental clinic in Pune" in block
    assert "- Key policies: Free first consult" in block
    # Unset fields are omitted entirely
    assert "Target customers" not in block


def test_format_business_profile_ignores_whitespace_only():
    ctx = {"business_profile": {"business_description": "   "}}
    assert format_business_profile(ctx) == ""


# ─── Source context sanitizer ────────────────────────────────────────────


def test_sanitize_source_context_empty():
    assert sanitize_source_context(None) == {}
    assert sanitize_source_context("   ") == {}


def test_sanitize_source_context_trims_and_caps():
    long_desc = "x" * 5000
    cleaned = sanitize_source_context(
        description=long_desc,
        category="  Pricing  ",
        questions_to_answer=["  q1 ", "", "q2"],
        extra={"not_covered": "y" * 5000, "pages_that_matter": "/pricing"},
    )
    assert len(cleaned["source_description"]) == 2000
    assert cleaned["source_category"] == "Pricing"
    assert cleaned["questions_to_answer"] == ["q1", "q2"]
    assert len(cleaned["not_covered"]) == 2000
    assert cleaned["pages_that_matter"] == "/pricing"


def test_sanitize_source_context_question_cap():
    qs = [f"q{i}" for i in range(50)]
    cleaned = sanitize_source_context("desc", questions_to_answer=qs)
    assert len(cleaned["questions_to_answer"]) == 20


def test_sanitize_source_context_ignores_unknown_extra_keys():
    cleaned = sanitize_source_context(
        "desc", extra={"evil_key": "x" * 100, "pages_that_matter": "/ok"}
    )
    assert "evil_key" not in cleaned
    assert cleaned["pages_that_matter"] == "/ok"


# ─── Chunk metadata source label ─────────────────────────────────────────


def test_format_source_context_line_variants():
    assert format_source_context_line(None) == ""
    assert format_source_context_line({}) == ""
    assert format_source_context_line({"title": "Home"}) == "Home"
    md = {"source_description": "Price list", "source_category": "Pricing"}
    assert format_source_context_line(md) == "Price list — Pricing"


# ─── System prompt injection ─────────────────────────────────────────────

FIELD_DEFS = [{"name": "name", "type": "text", "required": True}]


def test_system_prompt_includes_business_block():
    org_config = {
        "timezone": "UTC",
        "knowledge_context": {
            "business_profile": {"business_description": "Dental clinic"}
        },
    }
    prompt = build_system_prompt(org_config, FIELD_DEFS)
    assert "ABOUT THE BUSINESS" in prompt
    assert "Dental clinic" in prompt
    # Core booking instructions remain intact
    assert "OUTPUT FORMAT" in prompt
    assert "REQUIRED FIELDS" in prompt


def test_system_prompt_without_knowledge_context():
    prompt = build_system_prompt({"timezone": "UTC"}, FIELD_DEFS)
    assert "ABOUT THE BUSINESS" not in prompt


# ─── Legacy pipeline injection helper contract ───────────────────────────


def _legacy_persona_injection(custom_prompts, knowledge_context):
    """Mirror of message_processor's injection block — kept as a contract
    test so the shallow-copy semantics cannot silently regress."""
    persona = custom_prompts if isinstance(custom_prompts, dict) else {}
    if isinstance(knowledge_context, dict):
        block = format_business_profile(knowledge_context)
        if block:
            persona = dict(persona)
            persona["custom_instructions"] = (
                str(persona.get("custom_instructions") or "").rstrip()
                + ("\n" if persona.get("custom_instructions") else "")
                + block.replace("## ABOUT THE BUSINESS", "ABOUT THE BUSINESS")
            ).strip()
    return persona


def test_legacy_persona_injection_appends_profile():
    custom_prompts = {"agent_name": "Riya", "custom_instructions": "Be brief"}
    knowledge_ctx = {
        "business_profile": {"business_description": "Dental clinic"}
    }
    persona = _legacy_persona_injection(custom_prompts, knowledge_ctx)

    # Copy was mutated, original untouched (protects ORM JSONB state)
    assert "ABOUT THE BUSINESS" in persona["custom_instructions"]
    assert "Dental clinic" in persona["custom_instructions"]
    assert "Be brief" in persona["custom_instructions"]
    assert custom_prompts == {"agent_name": "Riya", "custom_instructions": "Be brief"}


def test_legacy_persona_injection_noop_without_context():
    persona = _legacy_persona_injection({"agent_name": "Riya"}, None)
    assert persona == {"agent_name": "Riya"}
