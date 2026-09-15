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

_KNOWLEDGE_QUERY_TOKEN_RE = re.compile(
    r"\b(what|which|when|where|who|whom|whose|why|how|can|could|would|should|will|"
    r"do|does|did|tell me|explain|offer|offers|services|service|hours|pricing|price|"
    r"cost|available|open|policy|policies|summary|summarize|summarise|detail|details|"
    r"information|info|about|know|list|features|benefits|overview|introduce)\b",
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

# Mercury (Inception Labs) uses reasoning_effort param; pass only for that family
if "mercury" in settings.LLM_MODEL.lower():
    llm = ChatOpenAI(
        base_url=_normalized_llm_base_url,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0,
        reasoning_effort="low",
    )
else:
    llm = ChatOpenAI(
        base_url=_normalized_llm_base_url,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        temperature=0,
    )

# Fallback LLM — used when the primary provider fails (rate limit, timeout,
# 5xx). Disabled entirely when the fallback env vars are empty.
fallback_llm = None
if settings.LLM_FALLBACK_BASE_URL and settings.LLM_FALLBACK_API_KEY:
    fallback_llm = ChatOpenAI(
        base_url=_normalize_llm_base_url(settings.LLM_FALLBACK_BASE_URL),
        api_key=settings.LLM_FALLBACK_API_KEY,
        model=settings.LLM_FALLBACK_MODEL or settings.LLM_MODEL,
        temperature=0,
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

    active_summary, active_messages, context_compacted = _compact_context_messages(
        messages=list(state.get("messages", [])),
        summary=str(state.get("summary", "") or ""),
        max_active_messages=MAX_ACTIVE_CONTEXT_MESSAGES,
    )

    latest_user_message = ""
    for message in reversed(active_messages):
        if isinstance(message, HumanMessage):
            latest_user_message = _message_content(message).strip()
            if latest_user_message:
                break

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
                "otp_required": bool(state.get("otp_required", False)),
                "otp_event_key": str(state.get("otp_event_key") or ""),
                "otp_code": str(state.get("otp_code") or ""),
                "schedule_answer": schedule_answer,
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
    sys_prompt = build_system_prompt(org_config, field_definitions, hours_block=hours_block)
    context_block = build_context_block(collected, missing, "ACTIVE")
    if active_summary:
        context_block = f"{context_block}\n\nConversation summary:\n{active_summary}"

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
            if _source_ref:
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
                from app.services.retrieval_service import (
                    get_document_overview,
                    list_knowledge_sources,
                )
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

            if rag_chunks:
                from app.engine.knowledge_profile import format_source_context_line
                logger.info(
                    f"RAG_DEBUG injecting chunks={len(rag_chunks)} overview={overview_mode}"
                )
                knowledge_lines = []
                for i, ch in enumerate(rag_chunks, 1):
                    content = (ch.get("content") or "").strip()
                    source = ch.get("source_type") or "document"
                    ctx_label = format_source_context_line(ch.get("metadata"))
                    if ctx_label:
                        source += f" — {ctx_label}"
                    if ch.get("source_url"):
                        source += f" ({ch['source_url']})"
                    snippet = content[:1000]
                    knowledge_lines.append(f"[{i}] (source: {source})\n{snippet}")
                knowledge = "\n\n".join(knowledge_lines)
                grounding = (
                    " If the answer is NOT contained in these materials, say "
                    "plainly that you don't have that information — NEVER guess "
                    "or invent names, authors, dates or facts."
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

        # Fallback mode: try the backup LLM before giving up to the canned
        # message. Both providers failing is the only path that yields the
        # canned reply.
        if fallback_llm is not None:
            try:
                response = await asyncio.wait_for(
                    fallback_llm.ainvoke(messages),
                    timeout=MAIN_LLM_HARD_TIMEOUT_SECONDS,
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
                fallback_message = (
                    f"Could I get your {'next detail' if missing else 'confirmation'}? "
                    "Please reply and I'll continue."
                )
                return {
                    "messages": [AIMessage(content=fallback_message)],
                    "summary": active_summary,
                    "context_window": [_serialize_message(message) for message in active_messages],
                    "context_window_size": len(active_messages),
                    "context_compacted": context_compacted,
                    "collected_fields": dict(state.get("collected_fields", {})),
                    "follow_up_fields": list(state.get("follow_up_fields", [])),
                    "current_intent": str(state.get("current_intent") or "START_BOOKING"),
                }
        else:
            fallback_message = (
                f"Could I get your {'next detail' if missing else 'confirmation'}? "
                "Please reply and I'll continue."
            )
            return {
                "messages": [AIMessage(content=fallback_message)],
                "summary": active_summary,
                "context_window": [_serialize_message(message) for message in active_messages],
                "context_window_size": len(active_messages),
                "context_compacted": context_compacted,
                "collected_fields": dict(state.get("collected_fields", {})),
                "follow_up_fields": list(state.get("follow_up_fields", [])),
                "current_intent": str(state.get("current_intent") or "START_BOOKING"),
            }
    
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
        unknown_fields = data.get("unknown_fields", {})
        reopen_fields = data.get("reopen_fields", [])

        required_fields = [
            field["name"] for field in field_definitions if field.get("required", True)
        ]
        optional_fields = [
            field["name"] for field in field_definitions if not field.get("required", True)
        ]

        validation_errors: Dict[str, str] = {}
        validated_extracted = dict(extracted) if isinstance(extracted, dict) else {}
        validated_metadata: Dict[str, Dict[str, str]] = {}
        extracted_for_validation = {
            key: value
            for key, value in validated_extracted.items()
            if key in {"email", "phone", "date", "time"}
        }
        date_reset_required = False
        fallback_reason = ""
        fallback_next_step = ""
        if extracted_for_validation:
            valid_entities, entity_errors, entity_metadata = DataValidator.validate_entities(
                extracted_for_validation,
                timezone=effective_timezone,
                include_metadata=True,
                field_definitions=None,
            )
            for key in list(extracted_for_validation.keys()):
                if key in valid_entities:
                    validated_extracted[key] = valid_entities[key]
                else:
                    validated_extracted.pop(key, None)
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

        merged_invalid_fields = dict(invalid_fields) if isinstance(invalid_fields, dict) else {}
        merged_invalid_fields.update(validation_errors)

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
        require_otp_raw = org_config.get("require_otp", True)
        require_otp = True if require_otp_raw is None else bool(require_otp_raw)
        otp_required = (
            require_otp
            and current_intent == "CONFIRM_BOOKING"
            and not otp_verified
            and not gated_follow_up_fields
        )
        otp_event_key = (
            _build_otp_event_key(str(state.get("session_id") or ""), merge_result["collected_fields"])
            if otp_required
            else ""
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
            "captured_deltas": merge_result["captured_deltas"],
            "merge_diagnostics": merge_result["diagnostics"],
            "validation_errors": validation_errors,
            "validated_field_metadata": validated_metadata,
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
