import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Tuple
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings
from app.core.telemetry import increment_usage
from app.engine.agent.state import BookingAgentState
from app.engine.agent.prompts import build_system_prompt, build_context_block
from app.engine.agent.context_builder import AgentContextBuilder
from app.engine.scheduling_policy import (
    lookup_schedule_business_hours_answer,
    resolve_effective_timezone_policy,
)
from app.engine.state_merge import apply_state_merge
from app.engine.validators import DataValidator

logger = logging.getLogger(__name__)

MAX_ACTIVE_CONTEXT_MESSAGES = 6
SUMMARY_CHAR_LIMIT = 2000
SUMMARY_ITEM_CHAR_LIMIT = 140
DATEISH_TOKEN_RE = re.compile(
    r"\b(today|tomorrow|next\s+\w+|this\s+\w+)\b|^\d{4}-\d{2}-\d{2}$|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$",
    flags=re.IGNORECASE,
)

# Hard ceilings so a slow provider degrades gracefully instead of blocking
# the customer chat for 30+ seconds (C4). Values are deliberate for a
# serverless/free-tier model; raise if a faster provider is configured.
RERANK_HARD_TIMEOUT_SECONDS = 6.0
MAIN_LLM_HARD_TIMEOUT_SECONDS = 35.0
FALLBACK_LLM_HARD_TIMEOUT_SECONDS = 12.0

_RAG_SKIP_PATTERNS = (
    re.compile(r"^\s*\d{1,2}(:\d{2})\s*(am|pm)?\s*$", flags=re.IGNORECASE),
    re.compile(r"^\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s*$"),
    re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$"),
    re.compile(r"^\s*\+?\d[\d\s().-]{6,}\s*$"),
    re.compile(r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$"),
    re.compile(r"^\s*virtual\|[\w-]+\|[\dTZ:.\-+]+(Z|\+\d{2}:\d{2})?\s*$"),
)

_CONFIRMATION_STARTER_RE = re.compile(
    r"^\s*(yes|yep|yeah|sure|ok|okay|alright|fine|perfect|great|thanks|thank you|"
    r"correct|confirmed|go ahead|please)([,.\s]|$)",
    flags=re.IGNORECASE,
)

# Website-vs-document source disambiguation for RAG grounding.
# "book" is deliberately NOT a document token here ("book an appointment").
_WEBSITE_SOURCE_RE = re.compile(
    r"\b(website|site|webpage|web page|homepage|url)\b",
    flags=re.IGNORECASE,
)
_DOC_SOURCE_RE = re.compile(
    r"\b(pdf|file|document|docs?|docx|novel|upload|uploaded|attachment|attached)\b",
    flags=re.IGNORECASE,
)

_KNOWLEDGE_QUERY_TOKEN_RE = re.compile(
    r"\b(what|which|when|where|who|whom|whose|why|how|can|could|would|should|will|"
    r"do|does|did|tell me|explain|offer|offers|services|service|hours|pricing|price|"
    r"cost|available|open|policy|policies|summary|summarize|summarise|detail|details|"
    r"information|info|about|know|list|features|benefits|overview|introduce)\b",
    flags=re.IGNORECASE,
)

# P1-6 AI anti-injection guardrail: strip common prompt-hijack phrases before LLM
_INJECTION_RE = re.compile(
    r"(?i)(ignore\s+previous\s+instructions?|ignore\s+all\s+instructions?|system\s*:\s*|jailbreak|do\s+not\s+follow\s+previous|bypass\s+safety)",
    flags=re.IGNORECASE,
)


def _sanitize_user_message(text: str) -> str:
    if not text:
        return text
    cleaned = _INJECTION_RE.sub("", text)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned.strip()[:2000]


# A bare calendar date, e.g. "30 September", "1 October", "2026-10-01",
# "Wednesday". These are booking field values, never knowledge questions, but
# the skip patterns below only listed US month names, so "30 September" fell
# through to the "rerank" tier and paid for a full LLM rerank round-trip on
# every date the customer picked.
_BARE_DATE_RE = re.compile(
    r"^\s*(?:"
    r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    r"(?:\s+\d{4})?"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"(?:,?\s+\d{4})?"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r")\s*$",
    flags=re.IGNORECASE,
)


def _looks_like_booking_field_value(message_text: str) -> bool:
    """True when a user message is a bare field value or short confirmation.

    Such turns cannot be knowledge questions, so skipping RAG avoids an
    extra LLM rerank round-trip (~5-20s) on every booking step (C4).
    """
    text = str(message_text or "").strip()
    if not text:
        return True
    if "?" in text:
        return False
    if _BARE_DATE_RE.match(text):
        return True
    if len(text.split()) <= 6 and _CONFIRMATION_STARTER_RE.match(text):
        return True
    return len(text.split()) <= 4 and any(
        pattern.search(text) for pattern in _RAG_SKIP_PATTERNS
    )


def _is_potential_knowledge_question(message_text: str) -> bool:
    """True when a turn plausibly asks for company/business knowledge.

    Booking statements ("I want to book an appointment"), field values and
    confirmations are never knowledge questions; running the LLM rerank on
    them wastes seconds per turn (C4).
    """
    text = str(message_text or "").strip()
    if not text:
        return False
    if text.endswith("?"):
        return True
    # Non-ASCII letters (Devanagari, Arabic, CJK, ...) — the English
    # question-token regex below cannot see them, so treat any such
    # message as a potential knowledge question.
    if any(ch.isalpha() and ord(ch) > 127 for ch in text):
        return True
    return bool(_KNOWLEDGE_QUERY_TOKEN_RE.search(text))


def _rag_tier(message_text: str) -> str:
    """Decide how much RAG work this turn deserves.

    Returns one of:
      - "skip":     definitely a booking field value / confirmation — no RAG.
      - "rerank":   clear knowledge question — BM25 + LLM rerank (accurate,
                    costs seconds).
      - "bm25_only": everything else (short noun phrases like a book title,
                    imperative requests, ambiguous turns) — run the cheap
                    indexed BM25 lookup and inject the top few chunks
                    directly, with no LLM rerank round-trip.

    The old binary gate silently starved RAG for messages like
    "summary of this" or a bare topic name, which ARE knowledge requests;
    BM25 alone is milliseconds, so the safe default is to run it.
    """
    text = str(message_text or "").strip()
    if not text or _looks_like_booking_field_value(text):
        return "skip"
    if _is_potential_knowledge_question(text):
        return "rerank"
    return "bm25_only"


# Backwards-compatible alias used by older call sites/tests.
def _should_skip_rag(message_text: str) -> bool:
    return _rag_tier(message_text) == "skip"


def _normalize_llm_base_url(raw_url: str) -> str:
    """Accept either OpenAI-compatible API root or full chat-completions URL."""
    normalized = str(raw_url or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if normalized.endswith(suffix):
        normalized = normalized[: -len(suffix)]
    return normalized


def _blocked_identity_overwrites(
    collected: Any,
    validated_extracted: Any,
    missing: Any,
    reopen_fields: Any,
    user_message: Any,
) -> List[str]:
    """Identity fields whose update must be dropped before the merge.

    Updates apply only when the field is still missing, explicitly
    reopened, or the turn's own message carries that kind of value (pure
    parsing of the message text — digit/email shape, never word lists).
    Date/time are exempt: users legitimately change them mid-flow and slot
    availability judges. Returns the list of blocked field names.
    """
    try:
        reopen_set = {
            str(item or "").strip().lower()
            for item in (reopen_fields or [])
            if isinstance(item, str) and item.strip()
        }
        msg_digits = re.sub(r"\D", "", str(user_message or ""))
        msg_shapes = {
            "phone": 10 <= len(msg_digits) <= 15,
            "email": bool(
                re.search(
                    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
                    str(user_message or ""),
                )
            ),
        }
        blocked: List[str] = []
        for id_field in ("phone", "email"):
            old_val = str((collected or {}).get(id_field) or "").strip()
            new_val = str((validated_extracted or {}).get(id_field) or "").strip()
            if (
                old_val
                and new_val
                and new_val != old_val
                and id_field not in (missing or [])
                and id_field not in reopen_set
                and not msg_shapes.get(id_field, False)
            ):
                blocked.append(id_field)
        return blocked
    except Exception:
        return []


# Initialize LLM
# Groq is OpenAI-compatible, so we use ChatOpenAI with base_url
_normalized_llm_base_url = _normalize_llm_base_url(settings.LLM_BASE_URL)
if _normalized_llm_base_url != settings.LLM_BASE_URL:
    logger.warning(
        "llm_base_url_normalized",
        extra={
            "provided_base_url": settings.LLM_BASE_URL,
            "normalized_base_url": _normalized_llm_base_url,
        },
    )

# Mercury (Inception Labs) uses reasoning_effort param; pass only for that family.
# Explicit retry/timeout on both clients: transient connection resets must be
# retried in-client before the graph pays for a full fallback-LLM round trip.
if "mercury" in settings.LLM_MODEL.lower():
    llm = ChatOpenAI(
        base_url=_normalized_llm_base_url,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0.5,
        reasoning_effort="low",
        max_retries=3,
        request_timeout=30.0,
        # Cap completion length: our replies are short by design (a few
        # hundred tokens), and an unbounded cap lets providers account the
        # model maximum against per-minute token budgets — a direct 429
        # driver on tight free tiers.
        max_tokens=1000,
    )
else:
    llm = ChatOpenAI(
        base_url=_normalized_llm_base_url,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0.5,
        max_retries=3,
        request_timeout=30.0,
        # Cap completion length: our replies are short by design (a few
        # hundred tokens), and an unbounded cap lets providers account the
        # model maximum against per-minute token budgets — a direct 429
        # driver on tight free tiers.
        max_tokens=1000,
    )

# Fallback LLM — used when the primary provider fails (rate limit, timeout,
# 5xx). Disabled entirely when the fallback env vars are empty.
fallback_llm = None
if settings.LLM_FALLBACK_BASE_URL and settings.LLM_FALLBACK_API_KEY:
    fallback_llm = ChatOpenAI(
        base_url=_normalize_llm_base_url(settings.LLM_FALLBACK_BASE_URL),
        api_key=settings.LLM_FALLBACK_API_KEY,
        model=settings.LLM_FALLBACK_MODEL or settings.LLM_MODEL,
        temperature=0.5,
        max_retries=3,
        request_timeout=30.0,
        # Same completion cap as primary (see above).
        max_tokens=1000,
    )
    logger.info(
        "llm_fallback_configured",
        extra={"fallback_model": fallback_llm.model_name},
    )


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    return "system"


def _message_content(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return str(content)


def _serialize_message(message: BaseMessage) -> Dict[str, str]:
    return {
        "role": _message_role(message),
        "content": _message_content(message),
    }


def _build_compaction_segment(messages: List[BaseMessage]) -> str:
    segments: List[str] = []
    for message in messages:
        role = _message_role(message)
        content = _message_content(message).strip()
        if not content:
            continue
        if role == "ai":
            # AI turns are raw structured JSON (response_text, extracted
            # fields, intents). Compacting the raw blob leaks internals into
            # future prompts and wastes the token budget — keep only what the
            # customer actually saw.
            try:
                from app.engine.user_visible_text import extract_user_visible_text

                content, _ = extract_user_visible_text(content)
                content = content.strip()
                if not content:
                    continue
            except Exception:
                pass
        clipped = content[:SUMMARY_ITEM_CHAR_LIMIT]
        segments.append(f"{role}: {clipped}")
    return " | ".join(segments)


def _merge_summary(existing_summary: str, compacted_segment: str) -> str:
    merged = "\n".join(
        part for part in [existing_summary.strip(), compacted_segment.strip()] if part
    )
    if len(merged) <= SUMMARY_CHAR_LIMIT:
        return merged
    return merged[-SUMMARY_CHAR_LIMIT:]


def _compact_context_messages(
    messages: List[BaseMessage],
    summary: str,
    max_active_messages: int,
) -> Tuple[str, List[BaseMessage], bool]:
    if len(messages) <= max_active_messages:
        return summary, messages, False

    overflow = messages[:-max_active_messages]
    active_messages = messages[-max_active_messages:]
    compacted_segment = _build_compaction_segment(overflow)
    merged_summary = _merge_summary(summary, compacted_segment)
    return merged_summary, active_messages, True


def _normalize_otp_component(raw_value: Any) -> str:
    return str(raw_value or "").strip().lower()


def _build_otp_event_key(session_id: str, collected_fields: Dict[str, Any]) -> str:
    email = _normalize_otp_component(collected_fields.get("email"))
    date_value = _normalize_otp_component(collected_fields.get("date"))
    time_value = _normalize_otp_component(collected_fields.get("time"))
    return f"otp:{session_id}:{email}:{date_value}:{time_value}"


def _looks_date_like(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(DATEISH_TOKEN_RE.search(text))


async def call_model(
    state: BookingAgentState,
    config: RunnableConfig | None = None,
):
    """The main LLM node that processes the conversation."""
    config_payload = config if isinstance(config, dict) else {}
    org_config = config_payload.get("configurable", {}).get("org_config", {})
    db = config_payload.get("configurable", {}).get("db")
    timezone_policy = resolve_effective_timezone_policy(
        requested_tz=str(state.get("user_timezone") or "") or None,
        org_tz=str(org_config.get("timezone") or "UTC"),
        requested_source="user_selected",
    )
    effective_timezone = str(timezone_policy.get("effective_timezone") or "UTC")
    max_booking_days = 90
    try:
        from app.engine.scheduling_policy import resolve_booking_horizon_days

        max_booking_days = (
            await resolve_booking_horizon_days(
                db,
                str(state.get("org_id") or ""),
                org_config,
            )
        )[0]
    except Exception:
        pass

    active_summary, active_messages, context_compacted = _compact_context_messages(
        messages=list(state.get("messages", [])),
        summary=str(state.get("summary", "") or ""),
        max_active_messages=MAX_ACTIVE_CONTEXT_MESSAGES,
    )

    latest_user_message = ""
    for message in reversed(active_messages):
        if isinstance(message, HumanMessage):
            latest_user_message = _sanitize_user_message(_message_content(message).strip())
            if latest_user_message:
                break

    # User behaviour understanding — every word before booking
    _behaviour_hint = ""
    _behaviour = {}
    try:
        from app.engine.user_behavior import analyze_user_behavior, behaviour_to_prompt_hint

        _hist_texts = [str(_message_content(m) or "").strip() for m in active_messages if isinstance(m, HumanMessage)]
        _behaviour = analyze_user_behavior(
            latest_user_message or "",
            history=_hist_texts[-4:],
            collected_data=dict(state.get("collected_fields", {}) or {}),
            missing_fields=list(state.get("follow_up_fields", []) or []),
        )
        _behaviour_hint = behaviour_to_prompt_hint(_behaviour)
    except Exception:
        _behaviour_hint = ""
        _behaviour = {}

    if latest_user_message:
        schedule_answer = await lookup_schedule_business_hours_answer(
            db=db,
            org_id=str(state.get("org_id") or ""),
            user_message=latest_user_message,
            effective_timezone=effective_timezone,
            org_timezone=str(org_config.get("timezone") or "UTC"),
            in_booking_flow=bool(state.get("collected_fields")),
        )
        if schedule_answer:
            policy_payload = {
                "response_text": schedule_answer["response_text"],
                "source": "policy",
                "schedule_answer": schedule_answer,
            }
            return {
                "messages": [AIMessage(content=json.dumps(policy_payload))],
                "summary": active_summary,
                "context_window": [_serialize_message(message) for message in active_messages],
                "context_window_size": len(active_messages),
                "context_compacted": context_compacted,
                "collected_fields": dict(state.get("collected_fields", {})),
                "optional_skips": list(state.get("optional_skips", [])),
                "pending_conflict": dict(state.get("pending_conflict", {})),
                "follow_up_fields": list(state.get("follow_up_fields", [])),
                "captured_deltas": dict(state.get("captured_deltas", {})),
                "merge_diagnostics": dict(state.get("merge_diagnostics", {})),
                "validation_errors": {},
                "validated_field_metadata": {},
                "current_intent": "ASK_QUESTION",
                "knowledge_answer": True,
                "otp_required": bool(state.get("otp_required", False)),
                "otp_event_key": str(state.get("otp_event_key") or ""),
                "otp_code": str(state.get("otp_code") or ""),
                "schedule_answer": schedule_answer,
            }

    # Available-dates inquiry ("which dates can I book?"): answer from the
    # live slot inventory, then continue the flow untouched. Without this,
    # such questions fall through to field collection and the user is
    # interrogated (e.g. asked for their phone number) instead of answered.
    if latest_user_message:
        from app.engine.scheduling_policy import (
            lookup_available_dates_answer as _lookup_dates,
        )

        dates_answer = await _lookup_dates(
            db=db,
            org_id=str(state.get("org_id") or ""),
            user_message=latest_user_message,
            effective_timezone=effective_timezone,
            org_timezone=str(org_config.get("timezone") or "UTC"),
        )
        if dates_answer:
            dates_payload = {
                "response_text": dates_answer["response_text"],
                "source": "availability",
                "availability_answer": dates_answer,
            }
            return {
                "messages": [AIMessage(content=json.dumps(dates_payload))],
                "summary": active_summary,
                "context_window": [_serialize_message(message) for message in active_messages],
                "context_window_size": len(active_messages),
                "context_compacted": context_compacted,
                "collected_fields": dict(state.get("collected_fields", {})),
                "optional_skips": list(state.get("optional_skips", [])),
                "pending_conflict": dict(state.get("pending_conflict", {})),
                "follow_up_fields": list(state.get("follow_up_fields", [])),
                "captured_deltas": dict(state.get("captured_deltas", {})),
                "merge_diagnostics": dict(state.get("merge_diagnostics", {})),
                "validation_errors": {},
                "validated_field_metadata": {},
                "current_intent": "ASK_QUESTION",
                "knowledge_answer": True,
                "otp_required": bool(state.get("otp_required", False)),
                "otp_event_key": str(state.get("otp_event_key") or ""),
                "otp_code": str(state.get("otp_code") or ""),
                "schedule_answer": dates_answer,
            }
    
    # 1. Build dynamic context
    context_builder = AgentContextBuilder(db, org_config)
    field_definitions = await context_builder.get_field_definitions()
    
    # 2. Extract missing fields based on current collected_fields
    collected = state.get("collected_fields", {})
    skipped_optional = set(state.get("optional_skips", []))
    missing = [
        f["name"]
        for f in field_definitions
        if f["name"] not in collected
        and (f.get("required", True) or f["name"] not in skipped_optional)
    ]
    
    # 3. Build system prompt
    from app.engine.scheduling_policy import load_weekly_hours_block

    hours_block = await load_weekly_hours_block(
        db, str(state.get("org_id") or "")
    )
    # First-turn signal for the greeting rule: none of the booking fields
    # collected yet (internal/metadata keys don't count). Deterministic
    # flag; the wording stays the model's job.
    _has_booking_data = False
    try:
        _collected_now = collected if isinstance(collected, dict) else {}
        _has_booking_data = any(
            str(_collected_now.get(_k) or "").strip()
            for _k in ("name", "email", "phone", "date", "time")
        )
    except Exception:
        _has_booking_data = False
    sys_prompt = build_system_prompt(
        org_config,
        field_definitions,
        hours_block=hours_block,
        is_first_turn=not _has_booking_data,
    )
    context_block = build_context_block(collected, missing, "ACTIVE")
    if active_summary:
        context_block = f"{context_block}\n\nConversation summary:\n{active_summary}"
    if _behaviour_hint:
        context_block = f"{context_block}\n\n{_behaviour_hint}"

    # 3b. Retrieve relevant knowledge from uploaded documents and crawled websites.
    # Tiered: booking field values/confirmations skip entirely ("skip");
    # clear knowledge questions get BM25 + LLM rerank ("rerank"); anything
    # else (bare topics, imperatives) still runs the cheap indexed BM25
    # lookup and injects the top few chunks without an LLM rerank call
    # ("bm25_only") so short requests like a book title are never starved.
    rag_tier = _rag_tier(latest_user_message) if latest_user_message else "skip"

    # Conversation-aware retrieval query: short follow-ups ("author?",
    # "what about the ending?") carry no topic on their own, so blend in
    # the user's recent turns to give keyword search real context.
    prior_user_texts = []
    for _m in active_messages[:-1]:
        if isinstance(_m, HumanMessage):
            _t = _message_content(_m).strip()
            if _t:
                prior_user_texts.append(_t[:200])
    retrieval_query = " ".join(
        prior_user_texts[-2:] + ([latest_user_message] if latest_user_message else [])
    ).strip() or (latest_user_message or "")

    logger.info(
        f"RAG_DEBUG tier={rag_tier} has_db={db is not None}"
    )
    from app.services.retrieval_service import references_uploaded_source
    if latest_user_message and db and rag_tier != "skip":
        try:
            if rag_tier == "rerank":
                from app.services.retrieval_service import retrieve_and_rerank
                rag_chunks = await retrieve_and_rerank(
                    db=db,
                    org_id=str(state.get("org_id", "")),
                    query=retrieval_query,
                    top_k=5,
                    candidate_k=30,
                )
            else:
                from app.services.retrieval_service import retrieve_chunks
                rag_chunks = await retrieve_chunks(
                    db=db,
                    org_id=str(state.get("org_id", "")),
                    query=retrieval_query,
                    k=3,
                )

            # Whole-document requests ("summarize the pdf", "this book")
            # must be answered from the DOCUMENT itself, not keyword hits:
            # generic words ("summary", "book") match unrelated website
            # pages and crowd out the real content. When the user names a
            # source — in this turn OR the recent conversation — lead with
            # its opening pages and keep only document-type keyword hits
            # alongside them.
            overview_mode = False
            # Source-reference detection, extended: the user may name the
            # document by its TITLE rather than the word "pdf"/"book"
            # ("the boy who never spoke", "who is the author"). If enough
            # filename words appear in the conversation query, treat it as
            # a source reference too.
            _source_ref = references_uploaded_source(retrieval_query)
            if not _source_ref:
                from app.services.retrieval_service import newest_doc_filename
                _fname = await newest_doc_filename(db, str(state.get("org_id", "")))
                if _fname:
                    import re as _re

                    def _sig_tokens(s: str) -> set:
                        return {
                            t.lower() for t in _re.findall(r"[^\W\d_]{4,}", s)
                        }

                    _title_toks = _sig_tokens(_fname.rsplit(".", 1)[0])
                    if len(_title_toks & _sig_tokens(retrieval_query)) >= 2:
                        _source_ref = True
            # Website-vs-document disambiguation: a query naming the WEBSITE
            # ("according to your website", "summarize your site") must be
            # answered from the crawled website chunks — never from an
            # uploaded document's opening pages. Only document references
            # take the overview path below.
            _website_only_ref = bool(
                _WEBSITE_SOURCE_RE.search(retrieval_query or "")
            ) and not bool(_DOC_SOURCE_RE.search(retrieval_query or ""))
            if _source_ref and not _website_only_ref:
                from app.services.retrieval_service import (
                    get_document_overview,
                    list_knowledge_sources,
                )
                overview = await get_document_overview(
                    db, str(state.get("org_id", ""))
                )
                doc_keyword_hits = [
                    c
                    for c in (rag_chunks or [])
                    if (c.get("source_type") or "") != "website"
                ]
                seen_ids = {c.get("id") for c in overview}
                merged = overview + [
                    c for c in doc_keyword_hits if c.get("id") not in seen_ids
                ]
                if merged:
                    rag_chunks = merged[:5]
                    overview_mode = True
                else:
                    # No documents at all — tell the agent what exists.
                    sources = await list_knowledge_sources(
                        db, str(state.get("org_id", ""))
                    )
                    if sources:
                        context_block = (
                            f"{context_block}\n\n"
                            f"## AVAILABLE KNOWLEDGE SOURCES\n"
                            f"The business has added these knowledge sources. "
                            f"Answer from them when relevant:\n"
                            + "\n".join(sources)
                        )
            elif not rag_chunks:
                # Keyword search found nothing. Questions like "who is the
                # author" or "when was it published" target document
                # METADATA that lives on the opening pages (title/copyright),
                # not in body keywords — so inject the overview pages plus
                # the sources list, and demand honest grounding.
                # Website-only questions skip the document overview (it would
                # inject an unrelated uploaded doc); the sources list still
                # tells the agent the website exists.
                from app.services.retrieval_service import (
                    get_document_overview,
                    list_knowledge_sources,
                )
                if _website_only_ref:
                    overview = []
                else:
                    overview = await get_document_overview(db, str(state.get("org_id", "")))
                sources = await list_knowledge_sources(db, str(state.get("org_id", "")))
                if overview:
                    rag_chunks = overview
                    overview_mode = True
                if sources:
                    context_block = (
                        f"{context_block}\n\n"
                        f"## AVAILABLE KNOWLEDGE SOURCES\n"
                        + "\n".join(sources)
                        + ("\n\nIMPORTANT: If the answer to the user's question is "
                           "not contained in the materials above, say plainly that "
                           "you don't have that information and offer to help "
                           "differently. NEVER guess or invent names, authors, "
                           "dates or facts."
                            if sources and not overview
                            else ""
                        )
                    )

            # Relevance gate (anti-hallucination): drop keyword hits sharing
            # no topical overlap with the question unless the LLM rerank
            # scored them relevant. Overview chunks bypass (user explicitly
            # named the document). A gated-out knowledge question falls into
            # the NO-KNOWLEDGE honesty block below instead of answering from
            # general knowledge with false confidence.
            if rag_chunks and not overview_mode and rag_tier != "skip":
                try:
                    from app.engine.knowledge_guards import (
                        gate_chunks_by_relevance as _gate_kb,
                    )

                    _gated, _dropped_all = _gate_kb(rag_chunks, retrieval_query)
                    if _dropped_all:
                        logger.info("RAG relevance gate dropped all chunks")
                    rag_chunks = _gated
                    # Distinctive-term check: the gate above can pass chunks on
                    # a generic verb ("offer", "provide", "services") while none
                    # of them mentions what the customer actually asked about.
                    # Production: "do you offer physiotherapy?" kept Azure
                    # chunks via "offer", and the answer read as yes. A
                    # distinctive query word (6+ chars) must appear in the kept
                    # material, or this is treated as no-knowledge and the
                    # honesty block below answers instead.
                    if rag_chunks:
                        try:
                            from app.engine.knowledge_guards import (
                                content_words as _kb_words,
                            )

                            _distinctive = {
                                w for w in _kb_words(retrieval_query) if len(w) >= 6
                            }
                            if _distinctive:
                                _pooled = " ".join(
                                    f"{c.get('content') or ''} {c.get('snippet') or ''}"
                                    for c in rag_chunks
                                )
                                if not (_distinctive & _kb_words(_pooled)):
                                    logger.info(
                                        "RAG distinctive-term check dropped all chunks"
                                    )
                                    rag_chunks = []
                        except Exception:
                            logger.warning(
                                "RAG distinctive-term check failed open", exc_info=True
                            )
                except Exception:
                    logger.warning("RAG relevance gating failed", exc_info=True)

            if rag_chunks:
                from app.engine.knowledge_profile import format_source_context_line
                logger.info(
                    f"RAG_DEBUG injecting chunks={len(rag_chunks)} overview={overview_mode}"
                )
                knowledge_lines = []
                _skipped_fragments = 0
                for i, ch in enumerate(rag_chunks, 1):
                    content = (ch.get("content") or "").strip()
                    # Drop chunks that begin mid-sentence. The crawler
                    # occasionally splits a page and captures a fragment like
                    # "that person stacks, we use. NET, Azure..." from "...Do
                    # NOT be that person." Those fragments read as corruption
                    # and the model repeats them verbatim, so they are removed
                    # rather than shown as if they were company content.
                    if not content or not content[0].isupper():
                        _skipped_fragments += 1
                        continue
                    source = ch.get("source_type") or "document"
                    ctx_label = format_source_context_line(ch.get("metadata"))
                    if ctx_label:
                        source += f" — {ctx_label}"
                    if ch.get("source_url"):
                        source += f" ({ch['source_url']})"
                    snippet = content[:1000]
                    knowledge_lines.append(f"[{i}] (source: {source})\n{snippet}")
                if _skipped_fragments:
                    logger.info(
                        "RAG_DEBUG skipped %s mid-sentence chunk fragment(s)",
                        _skipped_fragments,
                    )
                knowledge = "\n\n".join(knowledge_lines)
                # These chunks WERE retrieved for this question, so treat them
                # as the answer rather than as a hint to second-guess. The
                # previous wording ("if the answer is NOT contained in these
                # materials, say you don't have it") made a cautious model
                # hedge on questions its own search had just answered, because
                # the excerpt was a partial page rather than the full sentence.
                # Anti-hallucination is kept, but aimed at facts with no
                # support anywhere in the chunks.
                from app.engine.business_identity import (
                    KNOWLEDGE_IDENTITY_NOTE as _IDENTITY_NOTE,
                )
                grounding = (
                    " These excerpts were retrieved because they are the most "
                    "relevant material the company has for this question. If "
                    "they cover the topic, answer directly from them — a "
                    "relevant excerpt counts as an answer even when the page "
                    "did not repeat your exact question. If they genuinely do "
                    "not cover it, say so plainly. Either way, state only "
                    "things supported by these excerpts; never invent names, "
                    "dates, prices or details. " + _IDENTITY_NOTE
                )
                if overview_mode:
                    header = (
                        "The following are the OPENING PAGES of the business's document. "
                        "Use them to answer questions about the document itself "
                        "(summaries, topic, title, author)." + grounding
                    )
                elif rag_tier == "bm25_only":
                    header = (
                        "The user's message may be referring to one of the business's "
                        "materials below (for example a document title or topic) rather "
                        "than expressing feelings. Use them to respond helpfully."
                        + grounding
                    )
                else:
                    header = (
                        "The following information is from the company's business documents "
                        "and website. Use it to answer the user's question if relevant."
                        + grounding
                    )
                context_block = (
                    f"{context_block}\n\n"
                    f"## COMPANY KNOWLEDGE\n{header}\n"
                    f"{knowledge}"
                )

            # Hard honesty rule: the user asked a knowledge question but NO
            # company materials matched (no chunks, no overview, no sources).
            # Without this the model answers from general knowledge and
            # hallucinates. Booking/smalltalk turns are unaffected.
            _kb_injected = bool(locals().get("rag_chunks")) or (
                "AVAILABLE KNOWLEDGE SOURCES" in context_block
            )
            if not _kb_injected and _is_potential_knowledge_question(
                latest_user_message or ""
            ):
                context_block = (
                    f"{context_block}\n\n"
                    "## NO KNOWLEDGE FOUND\n"
                    "NONE of the company's materials answer this question. "
                    "You are a BOOKING ASSISTANT, not a general-purpose "
                    "assistant.\n"
                    "Rules:\n"
                    "- Answer ONLY from the materials above. If they do not "
                    "contain the answer, you do not have it.\n"
                    "- NEVER answer general-knowledge questions (trivia, "
                    "geography, science, news, politics, coding) from your own "
                    "memory. You have no reliable source for them and you WILL "
                    "get them wrong — a made-up answer is far worse than "
                    "admitting ignorance.\n"
                    "- Say it warmly and briefly, in your own words, e.g. "
                    "\"I'm a booking assistant, so I can only help with "
                    "appointments — I don't have information about that.\"\n"
                    "- Then offer what you CAN do: book, check, reschedule or "
                    "cancel an appointment.\n"
                    "- Do NOT tell the user to search elsewhere, and do NOT "
                    "mention \"knowledgebase\", \"materials\" or \"documents\" — "
                    "that reads as a brush-off."
                )
        except Exception:
            logger.warning("RAG retrieval failed", exc_info=True)
    
    full_system_msg = SystemMessage(content=f"{sys_prompt}\n\n{context_block}")
    
    # 4. Invoke LLM
    # In LangGraph, we prepend the system message to the message history
    messages = [full_system_msg] + active_messages
    
    try:
        response = await asyncio.wait_for(
            llm.ainvoke(messages),
            timeout=MAIN_LLM_HARD_TIMEOUT_SECONDS,
        )
    except Exception as llm_error:
        is_timeout = isinstance(llm_error, asyncio.TimeoutError)
        if is_timeout:
            logger.warning(
                "main_llm_hard_timeout",
                extra={
                    "session_id": state.get("session_id"),
                    "timeout_seconds": MAIN_LLM_HARD_TIMEOUT_SECONDS,
                },
            )
        else:
            logger.warning("main_llm_call_failed", exc_info=True)

        # Fallback mode: try the backup LLM before giving up. If both providers
        # fail, try regex fallback extraction (so booking can still progress
        # without an LLM — the "temporary issue" path). Only if that also
        # yields nothing do we raise for the ConversationManager legacy fallback.
        if fallback_llm is not None:
            try:
                response = await asyncio.wait_for(
                    fallback_llm.ainvoke(messages),
                    timeout=FALLBACK_LLM_HARD_TIMEOUT_SECONDS,
                )
                logger.warning(
                    "main_llm_fallback_used",
                    extra={
                        "session_id": state.get("session_id"),
                        "fallback_model": fallback_llm.model_name,
                        "primary_error": str(llm_error)[:500],
                    },
                )
            except Exception:
                logger.warning("main_llm_fallback_failed", exc_info=True)
                # Try regex fallback before raising
                try:
                    from app.engine.message_processor import _extract_entities_without_llm as _fallback_extract
                    from app.engine.message_processor import _build_llm_recovery_message as _fallback_msg
                    from app.engine.evaluator import StateEvaluator as _SE
                    from app.schemas.agent import parse_field_definitions as _parse_fds

                    _fds = _parse_fds(field_definitions)
                    _fallback_extracted = _fallback_extract(latest_user_message, missing, _fds, effective_timezone, max_booking_days)
                    if _fallback_extracted:
                        # Validate & merge like the normal path
                        from app.engine.state_merge import apply_state_merge as _apply_merge

                        _required = [f.name for f in _fds if f.required]
                        _optional = [f.name for f in _fds if not f.required]
                        _merge = _apply_merge(
                            current_fields=collected,
                            extracted_fields=_fallback_extracted,
                            required_fields=_required,
                            optional_fields=_optional,
                            optional_skips=set(state.get("optional_skips", [])),
                            invalid_fields={},
                            skipped_fields=[],
                            conflicts={},
                            unknown_fields={},
                            reopen_fields=[],
                        )
                        _new_missing = list(_merge["follow_up_fields"])
                        # Recompute directive for recovery message
                        _next = _SE.determine_next_action(_new_missing, str(state.get("current_intent") or "START_BOOKING"), _fds)
                        _directive = _next.get("directive", "TEXT_INPUT")
                        _field_meta = _next.get("field_meta")
                        _recovery = _fallback_msg(_directive, _field_meta)
                        return {
                            "messages": [AIMessage(content=_recovery)],
                            "summary": active_summary,
                            "context_window": [_serialize_message(m) for m in active_messages],
                            "context_window_size": len(active_messages),
                            "context_compacted": context_compacted,
                            "collected_fields": _merge["collected_fields"],
                            "optional_skips": sorted(_merge["optional_skips"]),
                            "pending_conflict": _merge["pending_conflict"],
                            "follow_up_fields": _new_missing,
                            "captured_deltas": _merge["captured_deltas"],
                            "merge_diagnostics": _merge["diagnostics"],
                            "validation_errors": {},
                            "validated_field_metadata": {},
                            "current_intent": str(state.get("current_intent") or "START_BOOKING"),
                        }
                    # No valid extraction — still return a recovery message for the current missing field
                    try:
                        from app.engine.evaluator import StateEvaluator as _SE_fallback
                        _next_f = _SE_fallback.determine_next_action(missing, str(state.get("current_intent") or "START_BOOKING"), _fds)
                        _recovery_f = _fallback_msg(_next_f.get("directive", "TEXT_INPUT"), _next_f.get("field_meta"))
                        return {
                            "messages": [AIMessage(content=_recovery_f)],
                            "summary": active_summary,
                            "context_window": [_serialize_message(m) for m in active_messages],
                            "context_window_size": len(active_messages),
                            "context_compacted": context_compacted,
                            "collected_fields": dict(state.get("collected_fields", {})),
                            "follow_up_fields": list(missing),
                            "current_intent": str(state.get("current_intent") or "START_BOOKING"),
                        }
                    except Exception:
                        pass
                except Exception as _fe:
                    logger.warning(f"fallback regex extraction failed: {_fe}", exc_info=True)
                raise TimeoutError(f"LLM outage (primary {type(llm_error).__name__} + fallback failed)") from llm_error
        else:
            # No backup LLM — try regex fallback immediately
            try:
                from app.engine.message_processor import _extract_entities_without_llm as _fallback_extract2
                from app.engine.message_processor import _build_llm_recovery_message as _fallback_msg2
                from app.engine.evaluator import StateEvaluator as _SE2
                from app.schemas.agent import parse_field_definitions as _parse_fds2

                _fds2 = _parse_fds2(field_definitions)
                _fallback_extracted2 = _fallback_extract2(latest_user_message, missing, _fds2, effective_timezone, max_booking_days)
                if _fallback_extracted2:
                    from app.engine.state_merge import apply_state_merge as _apply_merge2

                    _required2 = [f.name for f in _fds2 if f.required]
                    _optional2 = [f.name for f in _fds2 if not f.required]
                    _merge2 = _apply_merge2(
                        current_fields=collected,
                        extracted_fields=_fallback_extracted2,
                        required_fields=_required2,
                        optional_fields=_optional2,
                        optional_skips=set(state.get("optional_skips", [])),
                        invalid_fields={},
                        skipped_fields=[],
                        conflicts={},
                        unknown_fields={},
                        reopen_fields=[],
                    )
                    _new_missing2 = list(_merge2["follow_up_fields"])
                    _next2 = _SE2.determine_next_action(_new_missing2, str(state.get("current_intent") or "START_BOOKING"), _fds2)
                    _directive2 = _next2.get("directive", "TEXT_INPUT")
                    _field_meta2 = _next2.get("field_meta")
                    _recovery2 = _fallback_msg2(_directive2, _field_meta2)
                    return {
                        "messages": [AIMessage(content=_recovery2)],
                        "summary": active_summary,
                        "context_window": [_serialize_message(m) for m in active_messages],
                        "context_window_size": len(active_messages),
                        "context_compacted": context_compacted,
                        "collected_fields": _merge2["collected_fields"],
                        "optional_skips": sorted(_merge2["optional_skips"]),
                        "pending_conflict": _merge2["pending_conflict"],
                        "follow_up_fields": _new_missing2,
                        "captured_deltas": _merge2["captured_deltas"],
                        "merge_diagnostics": _merge2["diagnostics"],
                        "validation_errors": {},
                        "validated_field_metadata": {},
                        "current_intent": str(state.get("current_intent") or "START_BOOKING"),
                    }
                # No valid extraction — return recovery for current missing
                try:
                    _next_f2 = _SE2.determine_next_action(missing, str(state.get("current_intent") or "START_BOOKING"), _fds2)
                    _recovery_f2 = _fallback_msg2(_next_f2.get("directive", "TEXT_INPUT"), _next_f2.get("field_meta"))
                    return {
                        "messages": [AIMessage(content=_recovery_f2)],
                        "summary": active_summary,
                        "context_window": [_serialize_message(m) for m in active_messages],
                        "context_window_size": len(active_messages),
                        "context_compacted": context_compacted,
                        "collected_fields": dict(state.get("collected_fields", {})),
                        "follow_up_fields": list(missing),
                        "current_intent": str(state.get("current_intent") or "START_BOOKING"),
                    }
                except Exception:
                    pass
            except Exception as _fe2:
                logger.warning(f"fallback regex extraction failed2: {_fe2}", exc_info=True)
            raise TimeoutError(f"LLM outage: {type(llm_error).__name__}: {llm_error}") from llm_error
    
    # Emit AI token telemetry (gated no-op while TELEMETRY_ENABLED is false)
    usage_metadata = getattr(response, "usage_metadata", None)
    total_tokens = (
        usage_metadata.get("total_tokens")
        if isinstance(usage_metadata, dict)
        else None
    )
    if total_tokens:
        try:
            await increment_usage(
                uuid.UUID(str(state.get("org_id") or "")),
                "ai_tokens",
                delta=int(total_tokens),
            )
        except Exception:
            logger.warning("ai_tokens telemetry emission failed", exc_info=True)
    
    # 5. Extract structured data from response (if provided in JSON)
    try:
        content = str(response.content)
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "{" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            json_str = content[start:end]
        else:
            json_str = "{}"
            
        data = json.loads(json_str)
        extracted = data.get("extracted_fields", {})
        invalid_fields = data.get("invalid_fields", {})
        skipped_fields = data.get("skipped_fields", [])
        conflicts = data.get("conflicts", {})
        unknown_fields = data.get("unknown_fields", [])
        reopen_fields = data.get("reopen_fields", [])
        # Model-declared slot-list request: the customer asked for available
        # times/slots in their own words (any language/paraphrase) and the
        # renderer must answer with the computed inventory. Structured flag,
        # never keyword code.
        wants_slot_list = bool(data.get("wants_slot_list", False))

        # Name-grounding guard: company materials were injected this turn,
        # so every person-name-shaped phrase in the visible reply must occur
        # in them. Otherwise the model completed from training knowledge
        # (e.g. inventing leadership names) — swap in an honest decline
        # while keeping any extracted booking fields intact.
        # Skip when OTP is pending — the reply is OTP-related, not knowledge,
        # and the decline ghost ("Sorry I don't have...") must never replace it.
        _otp_pending_now = bool(state.get("otp_required") and not state.get("otp_verified"))
        if _otp_pending_now:
            _injected_chunks = []
        try:
            from app.engine.user_visible_text import (
                extract_user_visible_text as _extract_visible,
            )
            from app.engine.knowledge_guards import (
                find_ungrounded_names as _find_names,
            )

            _injected_chunks = locals().get("rag_chunks") or []
            if _injected_chunks and not _otp_pending_now:
                _visible_now, _ = _extract_visible(str(response.content))
                _chunk_texts = [
                    str((c or {}).get("content") or "")
                    for c in _injected_chunks
                    if isinstance(c, dict)
                ]
                _safe_now: List[str] = [str(org_config.get("business_name") or "")]
                _custom_prompts = org_config.get("custom_prompts") or {}
                if isinstance(_custom_prompts, dict):
                    _persona_now = _custom_prompts.get("persona") or {}
                    if _custom_prompts.get("agent_name"):
                        _safe_now.append(str(_custom_prompts.get("agent_name")))
                    if isinstance(_persona_now, dict) and _persona_now.get("agent_name"):
                        _safe_now.append(str(_persona_now.get("agent_name")))
                _bad_names = _find_names(
                    _visible_now,
                    _chunk_texts + [str(org_config.get("knowledge_context") or "")],
                    safe_names=tuple(name for name in _safe_now if name),
                )
                if _bad_names:
                    logger.warning(
                        "knowledge_name_gate_masked",
                        extra={
                            "session_id": state.get("session_id"),
                            "names": _bad_names,
                        },
                    )
                    # Strip only the invented names and keep the rest of the
                    # answer. The previous behaviour replaced the entire reply
                    # with a canned "I don't have that information" apology,
                    # which told the customer nothing and repeatedly hijacked
                    # in-progress booking conversations.
                    _current_text = str(data.get("response_text") or "")
                    for _bad in _bad_names:
                        _current_text = re.sub(
                            r"\b" + re.escape(str(_bad)) + r"\b",
                            "that person",
                            _current_text,
                            flags=re.IGNORECASE,
                        )
                    _current_text = re.sub(r"[ \t]{2,}", " ", _current_text).strip()
                    if not _current_text:
                        _current_text = (
                            "That detail isn't something I can confirm. "
                            "Could you tell me a little more about what you need?"
                        )
                    data["response_text"] = _current_text
                    response = AIMessage(content=json.dumps(data))
            # Answer-level grounding check: the composed reply must share
            # topical overlap with the chunks it claims to come from, or be an
            # explicit honest decline. Production: "do you offer
            # physiotherapy?" was answered "We specialize in Azure development
            # ..." from Azure chunks that never mention physiotherapy -- the
            # retrieval gate passed them on the generic verb "offer", and
            # nothing downstream ever checked the answer itself.
            # Scoped to knowledge questions only: a conversational turn
            # ("go on", "thanks") legitimately overlaps nothing, and
            # verifying it replaced a harmless acknowledgement with a
            # decline.
            try:
                from app.engine.knowledge_guards import (
                    verify_knowledge_answer as _verify_kb_answer,
                )

                _verify_chunks = locals().get("rag_chunks") or []
                # Only questions with something distinctive to ground can be
                # verified: "still there?" or "thanks" carry no 6+ letter
                # query word, so there is nothing to check the answer
                # against and verifying would replace harmless chat with
                # a decline. Same distinctive-word rule as the retrieval
                # gate above, applied to the question side.
                _verify_distinctive = False
                try:
                    from app.engine.knowledge_guards import (
                        content_words as _kb_qwords,
                    )

                    _verify_distinctive = bool({
                        w for w in _kb_qwords(retrieval_query or "")
                        if len(w) >= 6
                    })
                except Exception:
                    _verify_distinctive = True
                if (
                    _verify_chunks
                    and not _otp_pending_now
                    and _verify_distinctive
                    and _is_potential_knowledge_question(latest_user_message or "")
                ):
                    _verify_visible, _ = _extract_visible(str(response.content))
                    _verify_ok, _verify_reason = _verify_kb_answer(
                        _verify_visible, _verify_chunks
                    )
                    if not _verify_ok:
                        logger.warning(
                            "knowledge_answer_ungrounded",
                            extra={
                                "session_id": state.get("session_id"),
                                "reason": _verify_reason,
                            },
                        )
                        _verify_data = json.loads(str(response.content))
                        _verify_data["response_text"] = (
                            "I don't have information about that. "
                            "Is there anything I can help you book, check or "
                            "reschedule?"
                        )
                        _verify_data["knowledge_answer"] = True
                        response = AIMessage(content=json.dumps(_verify_data))
            except Exception:
                logger.warning("knowledge_answer_verify_failed_open", exc_info=True)
        except Exception:
            logger.warning("knowledge_name_gate_failed_open", exc_info=True)

        required_fields = [
            field["name"] for field in field_definitions if field.get("required", True)
        ]
        optional_fields = [
            field["name"] for field in field_definitions if not field.get("required", True)
        ]

        validation_errors: Dict[str, str] = {}
        validated_extracted = dict(extracted) if isinstance(extracted, dict) else {}
        validated_metadata: Dict[str, Dict[str, str]] = {}
        # "name" is validated too: without it a day-word first message
        # ("yesterday") sailed through merge and persisted as the customer's
        # name, since only contact/date fields were checked. The name
        # validator only rejects chitchat/digits/mash/date-words — real
        # names always pass.
        extracted_for_validation = {
            key: value
            for key, value in validated_extracted.items()
            if key in {"email", "phone", "date", "time", "name"}
        }
        # Time seatbelt: on terse time replies ("1:30pm", "10:30 AM") the
        # model intermittently chats without emitting any entity, stalling
        # the time step in a re-ask loop. When time is still needed and the
        # turn carries nothing else, the deterministic time grammar fills
        # it — the same parser validation uses, never a word list.
        # parse_flexible_time returns None for chatter, sentences ("my
        # preferred time is..."), phone numbers, and vague words
        # ("morning"), so this only fires on clean time strings, and the
        # normal validation + slot-availability checks still judge the value.
        if (
            "time" in (missing or [])
            and "time" not in validated_extracted
            and str(state.get("current_intent") or "START_BOOKING")
            in ("START_BOOKING", "PROVIDE_DATA")
        ):
            try:
                from app.engine.time_flex import parse_flexible_time as _pfl_seat

                if _pfl_seat(str(latest_user_message or "")) is not None:
                    _seat_time = str(latest_user_message or "").strip()
                    validated_extracted["time"] = _seat_time
                    extracted_for_validation["time"] = _seat_time
            except Exception:
                pass
        # Contact seatbelts (phone/email): same model-dropout class as time —
        # bare "6350063338" / "a@b.com" intermittently arrive with no entity
        # and the steps stall in re-ask loops. Shape parsing only (digit /
        # email grammar, never word lists); validation still judges.
        if str(state.get("current_intent") or "START_BOOKING") in (
            "START_BOOKING",
            "PROVIDE_DATA",
        ):
            try:
                _seat_msg = str(latest_user_message or "")
                if "phone" in (missing or []) and "phone" not in validated_extracted:
                    _seat_digits = re.sub(r"\D", "", _seat_msg)
                    if 10 <= len(_seat_digits) <= 15:
                        validated_extracted["phone"] = _seat_msg.strip()
                        extracted_for_validation["phone"] = _seat_msg.strip()
                if "email" in (missing or []) and "email" not in validated_extracted:
                    _seat_email = re.search(
                        r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
                        _seat_msg,
                    )
                    if _seat_email:
                        validated_extracted["email"] = _seat_email.group(0)
                        extracted_for_validation["email"] = _seat_email.group(0)
            except Exception:
                pass
        date_reset_required = False
        fallback_reason = ""
        fallback_next_step = ""
        if extracted_for_validation:
            # Admin's same-day policy enforced at date ENTRY: a "today" date
            # is rejected here with a clear reason instead of being accepted
            # and failing later at slot inventory (default deny, matching
            # _slot_policy_kwargs).
            _allow_same_day = bool(org_config.get("allow_same_day_booking", False))
            valid_entities, entity_errors, entity_metadata = DataValidator.validate_entities(
                extracted_for_validation,
                timezone=effective_timezone,
                max_booking_days=max_booking_days,
                include_metadata=True,
                field_definitions=None,
                allow_same_day_booking=_allow_same_day,
            )
            for key in list(extracted_for_validation.keys()):
                if key in valid_entities:
                    validated_extracted[key] = valid_entities[key]
                else:
                    validated_extracted.pop(key, None)
            # Raw-literal fallback: the model sometimes emits a decorated
            # phone/email variant (added spaces, guessed country code) that
            # fails validation while the user's literal text is shape-clean.
            # Prefer the literal — production: bare "9822014713" never stuck
            # because decorated variants kept failing validation.
            try:
                _raw_msg_fb = str(latest_user_message or "")
                if "phone" in entity_errors:
                    _fb_digits = re.sub(r"\D", "", _raw_msg_fb)
                    if 10 <= len(_fb_digits) <= 15:
                        _fb_res, _fb_err, _ = DataValidator._validate_phone(
                            _raw_msg_fb.strip()
                        )
                        if _fb_res and not _fb_err:
                            valid_entities["phone"] = _fb_res
                            validated_extracted["phone"] = _fb_res
                            entity_errors.pop("phone", None)
                            validation_errors.pop("phone", None)
                if "email" in entity_errors:
                    _fb_em = re.search(
                        r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
                        _raw_msg_fb,
                    )
                    if _fb_em:
                        _fb_eres, _fb_eerr = DataValidator._validate_email(
                            _fb_em.group(0)
                        )
                        if _fb_eres and not _fb_eerr:
                            valid_entities["email"] = _fb_eres
                            validated_extracted["email"] = _fb_eres
                            entity_errors.pop("email", None)
                            validation_errors.pop("email", None)
            except Exception:
                pass
            validation_errors.update(
                {
                    key: value
                    for key, value in entity_errors.items()
                    if isinstance(value, str) and value.strip()
                }
            )
            if isinstance(entity_metadata, dict):
                validated_metadata.update(entity_metadata)

            raw_time = extracted_for_validation.get("time")
            if (
                isinstance(validation_errors.get("time"), str)
                and _looks_date_like(raw_time)
            ):
                validation_errors.pop("time", None)
                validation_errors["date"] = (
                    "Please pick a valid date first, then choose an available time."
                )
                date_reset_required = True
                fallback_reason = "invalid_date_or_ambiguous_time_input"
                fallback_next_step = "pick_another_date"

            if isinstance(validation_errors.get("date"), str):
                date_reset_required = True
                if not fallback_reason:
                    fallback_reason = "invalid_date_input"
                    fallback_next_step = "pick_another_date"
                # Same-day refusal keeps its own reason code so the transport
                # shows the crisp policy message, not a generic invalid-date
                # prompt.
                if "Same-day booking isn't available" in str(validation_errors.get("date") or ""):
                    fallback_reason = "same_day_disabled"
                    fallback_next_step = "pick_another_date"

        merged_invalid_fields = dict(invalid_fields) if isinstance(invalid_fields, dict) else {}
        merged_invalid_fields.update(validation_errors)

        # Identity overwrite guard: a model-hallucinated phone/email that is
        # structurally valid must never silently replace a collected value.
        # Production: the customer's +91 number was overwritten by a
        # hallucinated +212 number (assembled from sender metadata) on a
        # turn that was not providing contact details — the OTP then went to
        # a number that does not exist and the customer could never verify.
        for _blocked_field in _blocked_identity_overwrites(
            collected=collected,
            validated_extracted=validated_extracted,
            missing=missing,
            reopen_fields=reopen_fields,
            user_message=latest_user_message,
        ):
            logger.warning(
                "identity_overwrite_blocked",
                extra={
                    "session_id": state.get("session_id"),
                    "field": _blocked_field,
                },
            )
            validated_extracted.pop(_blocked_field, None)

        merge_result = apply_state_merge(
            current_fields=collected,
            extracted_fields=validated_extracted,
            required_fields=required_fields,
            optional_fields=optional_fields,
            optional_skips=set(state.get("optional_skips", [])),
            invalid_fields=merged_invalid_fields,
            skipped_fields=skipped_fields,
            conflicts=conflicts,
            unknown_fields=unknown_fields,
            reopen_fields=reopen_fields,
        )

        gated_follow_up_fields = list(merge_result["follow_up_fields"])
        if date_reset_required:
            gated_follow_up_fields = [
                field for field in gated_follow_up_fields if field != "time"
            ]
            if "date" not in gated_follow_up_fields:
                gated_follow_up_fields.insert(0, "date")
        for core_field in ("phone", "email"):
            if core_field in validation_errors:
                if core_field in gated_follow_up_fields:
                    gated_follow_up_fields.remove(core_field)
                gated_follow_up_fields.insert(0, core_field)

        current_intent = str(state.get("current_intent") or "START_BOOKING")
        otp_verified = bool(state.get("otp_verified", False))
        phone_otp_verified = bool(state.get("phone_otp_verified", False))
        require_otp_raw = org_config.get("require_otp", True)
        require_otp = True if require_otp_raw is None else bool(require_otp_raw)
        require_phone_raw = org_config.get("require_phone_otp", False)
        require_phone_otp = False if require_phone_raw is None else bool(require_phone_raw)
        otp_required = (
            current_intent == "CONFIRM_BOOKING"
            and not gated_follow_up_fields
            and (
                (require_otp and not otp_verified)
                or (require_phone_otp and not phone_otp_verified)
            )
        )
        otp_event_key = (
            _build_otp_event_key(str(state.get("session_id") or ""), merge_result["collected_fields"])
            if otp_required
            else ""
        )

        # Knowledge-answer marker: company materials were injected into THIS
        # turn's prompt, so the visible reply may carry retrieved facts. The
        # chat transports render a pending booking step INSTEAD of the reply
        # unless they know an answer is inside — this flag tells them to show
        # both. Booking-only turns (RAG skipped) never set it.
        _knowledge_answer_now = bool(
            (locals().get("rag_tier") or "skip") != "skip"
            and locals().get("rag_chunks")
        )

        return {
            "messages": [response],
            "summary": active_summary,
            "context_window": [_serialize_message(message) for message in active_messages],
            "context_window_size": len(active_messages),
            "context_compacted": context_compacted,
            "collected_fields": merge_result["collected_fields"],
            "optional_skips": sorted(merge_result["optional_skips"]),
            "pending_conflict": merge_result["pending_conflict"],
            "follow_up_fields": gated_follow_up_fields,
            "wants_slot_list": wants_slot_list,
            "captured_deltas": merge_result["captured_deltas"],
            "merge_diagnostics": merge_result["diagnostics"],
            "validation_errors": validation_errors,
            "validated_field_metadata": validated_metadata,
            "knowledge_answer": _knowledge_answer_now,
            "date_reset_required": date_reset_required,
            "fallback_reason": fallback_reason,
            "fallback_next_step": fallback_next_step,
            "current_intent": current_intent,
            "otp_required": otp_required,
            "otp_event_key": otp_event_key,
            "otp_code": "",
        }
    except Exception as e:
        logger.error(f"Error parsing LLM response: {e}")
        return {
            "messages": [response],
            "summary": active_summary,
            "context_window": [_serialize_message(message) for message in active_messages],
            "context_window_size": len(active_messages),
            "context_compacted": context_compacted,
        }

# Define the graph
builder = StateGraph(BookingAgentState)

# Add nodes
builder.add_node("agent", call_model)

# Set entry point
builder.set_entry_point("agent")

# Add edges
builder.add_edge("agent", END)

# Compile with memory for persistent sessions
memory = MemorySaver()
booking_agent = builder.compile(checkpointer=memory)
