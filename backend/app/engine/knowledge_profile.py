"""Shared helpers for admin-collected knowledge context.

The knowledge context lives on ``org_configs.knowledge_context`` (JSONB):

    {
        "business_profile": {field: str, ...},
        "sources": {"<doc_id | 'website'>": {field: str | [str], ...}}
    }

Two consumers:
- ``format_business_profile`` renders a compact prompt block injected into
  the agent system prompt (LangGraph runtime and legacy pipeline).
- ``source_label_from_metadata`` renders a human-readable source label used
  when retrieved RAG chunks are formatted into prompts.
"""

from typing import Any, Dict, List, Optional

_BUSINESS_PROFILE_FIELDS = [
    ("business_name", "Business name"),
    ("business_description", "What the business does"),
    ("products_services", "Products / services offered"),
    ("target_customers", "Target customers"),
    ("key_policies", "Key policies"),
    ("answer_language_tone", "Answer language / tone"),
]

_SOURCE_CONTEXT_FIELDS = [
    ("source_description", "About this content"),
    ("source_category", "Category"),
]


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def get_business_profile(knowledge_context: Any) -> Dict[str, Any]:
    """Extract the business profile dict from an org's knowledge_context."""
    if not isinstance(knowledge_context, dict):
        return {}
    profile = knowledge_context.get("business_profile")
    return profile if isinstance(profile, dict) else {}


def format_business_profile(knowledge_context: Any) -> str:
    """Render the business profile as a compact prompt block.

    Returns "" when no meaningful profile fields are set.
    """
    profile = get_business_profile(knowledge_context)
    if not profile:
        return ""

    lines = []
    for key, label in _BUSINESS_PROFILE_FIELDS:
        value = _clean_str(profile.get(key))
        if value:
            lines.append(f"- {label}: {value}")

    if not lines:
        return ""

    return (
        "## ABOUT THE BUSINESS (provided by the business owner — treat as "
        "authoritative context; do not contradict it)\n" + "\n".join(lines)
    )


def get_source_context(knowledge_context: Any, source_key: str) -> Dict[str, Any]:
    """Look up per-source context collected before ingestion.

    ``source_key`` is a document id or ``"website"`` for crawled content.
    """
    if not isinstance(knowledge_context, dict):
        return {}
    sources = knowledge_context.get("sources")
    if not isinstance(sources, dict):
        return {}
    entry = sources.get(source_key)
    return entry if isinstance(entry, dict) else {}


def sanitize_source_context(
    description: Optional[str],
    category: Optional[str] = None,
    questions_to_answer: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize and cap admin-supplied source context before storage.

    Length caps keep prompt injection bounded even for hostile inputs.
    """
    cleaned: Dict[str, Any] = {}
    desc = _clean_str(description)[:2000]
    if desc:
        cleaned["source_description"] = desc
    category_clean = _clean_str(category)[:100]
    if category_clean:
        cleaned["source_category"] = category_clean
    if questions_to_answer:
        qs = [
            _clean_str(q)[:300]
            for q in questions_to_answer
            if _clean_str(q)
        ][:20]
        if qs:
            cleaned["questions_to_answer"] = qs
    if extra:
        for key in ("pages_that_matter", "not_covered"):
            val = _clean_str(extra.get(key))[:2000]
            if val:
                cleaned[key] = val
    return cleaned


def format_source_context_line(metadata: Any) -> str:
    """Render a chunk metadata dict into a short source annotation.

    Used by both runtimes when formatting retrieved chunks so the LLM knows
    what each excerpt came from. Returns "" when nothing useful is stored.
    """
    if not isinstance(metadata, dict):
        return ""
    parts = []
    for key, _label in _SOURCE_CONTEXT_FIELDS:
        value = _clean_str(metadata.get(key))
        if value:
            parts.append(value if len(parts) else value)
    if not parts:
        title = _clean_str(metadata.get("title"))
        return title
    return " — ".join(parts[:2])
