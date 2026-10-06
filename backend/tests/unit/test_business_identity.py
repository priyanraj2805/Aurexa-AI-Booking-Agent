"""Business/agent identity must come from Agent Personalization, never RAG.

Production failure: org "Priyanshu" never set org_config.business_name
(DB holds ""), the prompt builders used bare .get() defaults that do not
catch blank strings, and ~1000 crawled dotstark.com chunks sat in the
org's knowledge base. Nothing told the model where its identity comes
from, so it introduced itself as a booking agent for Dotstark.
"""
from app.engine.agent.prompts import build_system_prompt
from app.engine.business_identity import (
    KNOWLEDGE_IDENTITY_NOTE,
    identity_rule,
    resolve_agent_name,
    resolve_business_name,
)
from app.engine.prompts import PromptGenerator


def test_blank_business_name_falls_back_to_org_name() -> None:
    assert resolve_business_name({"business_name": ""}, "Priyanshu") == "Priyanshu"
    assert resolve_business_name({}, "Priyanshu") == "Priyanshu"
    assert resolve_business_name({"business_name": "  Handy Fix  "}, "Priyanshu") == (
        "Handy Fix"
    )


def test_business_name_defaults_when_nothing_set() -> None:
    assert resolve_business_name({}, "") == "our business"
    assert resolve_business_name(None, "") == "our business"


def test_agent_name_prefers_personalization() -> None:
    assert (
        resolve_agent_name({"custom_prompts": {"agent_name": "Aurexa"}}) == "Aurexa"
    )
    assert resolve_agent_name({"persona": {"agent_name": " receptionist "}}) == (
        "receptionist"
    )
    assert resolve_agent_name({}) == "Booking Assistant"


def test_reconcile_prefers_column_over_nested() -> None:
    from app.engine.business_identity import reconcile_business_names

    assert reconcile_business_names("Priyanshu", "BrightSmile") == (
        "Priyanshu",
        "Priyanshu",
    )
    assert reconcile_business_names("", "BrightSmile") == (
        "BrightSmile",
        "BrightSmile",
    )
    assert reconcile_business_names("", "") == ("", "")
    assert reconcile_business_names(None, None) == ("", "")


def test_identity_hijack_detected_only_on_self_claim() -> None:
    """Live failure: "Hello! I'm Aurexa, the booking assistant for Priyanshu
    at Dotstark." — prompt said Priyanshu, 1000 knowledge chunks said
    Dotstark, the model fused both. Mere discussion must pass."""
    from app.engine.business_identity import (
        enforce_identity,
        looks_like_identity_hijack,
    )

    for hijack in (
        "I am your booking assistant for Dotstark.",
        "Hello! I'm Aurexa, the booking assistant for Priyanshu at Dotstark.",
        "We at Dotstark offer 24/7 support.",
        # Greeting-shaped self-identification (production: turn 1 said
        # "Welcome to Dotstark", turn 2 "reaching out to Priyanshu").
        "Hello! Welcome to Dotstark.",
        "Thanks for reaching out to Dotstark.",
    ):
        assert looks_like_identity_hijack(hijack, "Priyanshu") is True
    for fine in (
        "Dotstark offers Kentico upgrades and Azure development.",
        "Thanks for reaching out to Priyanshu.",
        "I am Aurexa from Priyanshu. How can I help?",
        "We offer morning and evening slots.",
    ):
        assert looks_like_identity_hijack(fine, "Priyanshu") is False
    fixed = enforce_identity(
        "I am your booking assistant for Dotstark.", "Priyanshu", "Aurexa"
    )
    assert "Dotstark" not in fixed
    assert "Priyanshu" in fixed and "Aurexa" in fixed
    # Long answers are never rewritten, only short identity-style replies.
    long_answer = "Dotstark offers Kentico upgrades. " + ("More detail here. " * 30)
    assert enforce_identity(long_answer, "Priyanshu", "Aurexa") == long_answer


def test_identity_rule_pins_names_and_forbids_adoption() -> None:
    rule = identity_rule("Priyanshu", "Aurexa")
    assert "Priyanshu" in rule
    assert "Aurexa" in rule
    assert "NEVER adopt" in rule
    assert "COMPANY KNOWLEDGE" in rule


def test_graph_system_prompt_carries_identity() -> None:
    prompt = build_system_prompt(
        {"business_name": "", "timezone": "UTC"},
        [{"name": "name", "type": "text", "required": True}],
    )
    # Blank config must not produce "booking assistant for ." — the
    # resolver falls back to the default, never an empty name.
    assert "booking assistant for ." not in prompt
    assert "IDENTITY" in prompt
    assert "NEVER adopt" in prompt


def test_unified_prompt_carries_identity_and_rag_note() -> None:
    prompt = PromptGenerator.build_unified_turn_prompt(
        "hi",
        ["name"],
        {},
        "START_BOOKING",
        persona={"agent_name": "Aurexa"},
        business_name="",
        org_name="Priyanshu",
    )
    assert "Priyanshu" in prompt
    assert "IDENTITY" in prompt
    assert KNOWLEDGE_IDENTITY_NOTE in prompt
