import json
import logging
import re
from typing import Any, Dict, List, Tuple
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import settings
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

llm = ChatOpenAI(
    base_url=_normalized_llm_base_url,
    api_key=settings.LLM_API_KEY,
    model=settings.LLM_MODEL,
    temperature=0
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
    sys_prompt = build_system_prompt(org_config, field_definitions)
    context_block = build_context_block(collected, missing, "ACTIVE")
    if active_summary:
        context_block = f"{context_block}\n\nConversation summary:\n{active_summary}"

    # 3b. Retrieve relevant knowledge from uploaded documents and crawled websites
    if latest_user_message and db:
        try:
            from app.services.retrieval_service import retrieve_and_rerank
            rag_chunks = await retrieve_and_rerank(
                db=db,
                org_id=str(state.get("org_id", "")),
                query=latest_user_message,
                top_k=5,
                candidate_k=30,
            )
            if rag_chunks:
                knowledge_lines = []
                for i, ch in enumerate(rag_chunks, 1):
                    content = (ch.get("content") or "").strip()
                    source = ch.get("source_type") or "document"
                    if ch.get("source_url"):
                        source += f" ({ch['source_url']})"
                    snippet = content[:1000]
                    knowledge_lines.append(f"[{i}] (source: {source})\n{snippet}")
                knowledge = "\n\n".join(knowledge_lines)
                context_block = (
                    f"{context_block}\n\n"
                    f"## COMPANY KNOWLEDGE\n"
                    f"The following information is from the company's business documents "
                    f"and website. Use it to answer the user's question if relevant:\n"
                    f"{knowledge}"
                )
        except Exception:
            logger.warning("RAG retrieval failed", exc_info=True)
    
    full_system_msg = SystemMessage(content=f"{sys_prompt}\n\n{context_block}")
    
    # 4. Invoke LLM
    # In LangGraph, we prepend the system message to the message history
    messages = [full_system_msg] + active_messages
    
    response = await llm.ainvoke(messages)
    
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
            if core_field in validation_errors and core_field not in gated_follow_up_fields:
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
