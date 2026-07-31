import logging
import re
from typing import Any, Dict, Optional
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from langchain_core.messages import HumanMessage, AIMessage

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.booking import BusinessSchedule
from app.schemas.agent import ChatSessionState, ActionPayload
from app.crud import chat as crud_chat
from app.crud import otp as otp_crud
from app.core import mail as mailer
from app.engine.checkpoint_manager import CheckpointManager
from app.engine.agent.graph import booking_agent
from app.engine.evaluator import StateEvaluator
from app.engine import message_processor
from app.engine.recipient_resolution import resolve_email_from_collected_data
from app.engine.user_visible_text import extract_user_visible_text
from app.schemas.agent import FieldDefinition, parse_field_definitions, order_field_definitions

logger = logging.getLogger(__name__)
MAX_RUNTIME_CONTEXT_MESSAGES = 6
OTP_COOLDOWN_SECONDS = 30
OTP_RESEND_CAP = 3
OTP_VALID_FOR_SECONDS = 300
CONFIRMATION_CLAIM_PATTERN = re.compile(
    r"\b("
    r"booking\s+(is\s+)?confirm(ed|ation)|"
    r"appointment\s+(is\s+)?confirm(ed|ation)|"
    r"reservation\s+(is\s+)?(saved|confirm(ed|ation))|"
    r"successfully\s+booked|"
    r"your\s+slot\s+is\s+booked"
    r")\b",
    flags=re.IGNORECASE,
)


def _extract_user_visible_message(raw_message: Any) -> tuple[str, Dict[str, Any]]:
    return extract_user_visible_text(raw_message)


async def _persist_transcript_best_effort(
    db: AsyncSession,
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
    directive: str,
    intent: Optional[str],
) -> None:
    if not hasattr(db, "add") or not hasattr(db, "commit"):
        return
    try:
        await crud_chat.save_message(db, session_id, "user", user_message)
        metadata: Dict[str, Any] = {"directive": directive}
        if intent:
            metadata["intent"] = intent
        await crud_chat.save_message(
            db, session_id, "agent", assistant_message, metadata=metadata
        )
    except Exception as exc:  # pragma: no cover - best effort parity with legacy path
        logger.warning("Failed to persist graph transcript: %s", exc)


def _active_effect_state(chat_session: ChatSessionState) -> Dict[str, Any]:
    data = dict(chat_session.collected_data or {})
    state = data.get("_active_path_effects")
    if not isinstance(state, dict):
        state = {}
    data["_active_path_effects"] = state
    chat_session.collected_data = data
    return state


_RESEND_PATTERN = re.compile(
    r"\b(resend|re-?send|send\s+(?:it|the\s+code|again)|new\s+code|didn'?t\s+(?:get|receive))\b",
    re.IGNORECASE,
)


def _is_resend_request(message_text: str) -> bool:
    text = str(message_text or "").strip()
    if not text:
        return False
    return bool(_RESEND_PATTERN.search(text))


def _is_awaiting_otp(chat_session: ChatSessionState) -> bool:
    if getattr(chat_session, "otp_verified", False):
        return False
    effect_state = _active_effect_state(chat_session)
    sent_keys = effect_state.get("otp_sent_keys")
    return isinstance(sent_keys, list) and len(sent_keys) > 0


async def _handle_graph_otp_resend(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    return await message_processor._handle_otp_resend(
        db, org, chat_session, message_text, background_tasks
    )


def _normalize_event_component(raw_value: Any) -> str:
    return str(raw_value or "").strip().lower()


def _build_confirmation_event_key(session_id: str, collected_data: Dict[str, Any]) -> str:
    booking_id = _normalize_event_component(collected_data.get("booking_id"))
    if booking_id:
        return f"confirmation:{session_id}:{booking_id}"
    email = _normalize_event_component(collected_data.get("email"))
    date_value = _normalize_event_component(collected_data.get("date"))
    time_value = _normalize_event_component(collected_data.get("time"))
    return f"confirmation:{session_id}:{email}:{date_value}:{time_value}"


def _normalize_collected_data(raw_collected_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(raw_collected_data, dict):
        return {}
    sanitized = dict(raw_collected_data)
    sanitized.pop(crud_chat.RUNTIME_CHECKPOINT_KEY, None)
    return sanitized


def _normalize_context_window(raw_window: Any) -> list[dict[str, str]]:
    if not isinstance(raw_window, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in raw_window:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = item.get("content")
        if role not in {"human", "ai", "user", "assistant"}:
            continue
        if content is None:
            continue
        normalized.append({"role": role, "content": str(content)})
    return normalized


def _window_item_to_message(item: dict[str, str]) -> HumanMessage | AIMessage:
    role = item.get("role", "human")
    content = item.get("content", "")
    if role in {"ai", "assistant"}:
        return AIMessage(content=content)
    return HumanMessage(content=content)


def _load_runtime_context(chat_session: ChatSessionState) -> tuple[str, list[dict[str, str]]]:
    raw_state = {}
    if isinstance(chat_session.collected_data, dict):
        checkpoint = chat_session.collected_data.get(crud_chat.RUNTIME_CHECKPOINT_KEY, {})
        if isinstance(checkpoint, dict):
            raw_state = checkpoint.get("state", {}) or {}
    summary = str(raw_state.get("summary", "") or "")
    context_window = _normalize_context_window(raw_state.get("context_window"))
    return summary, context_window


def _resolve_field_definitions_snapshot(
    chat_session: ChatSessionState,
    org_config_dict: Dict[str, Any],
) -> list[FieldDefinition]:
    snapshot_raw = {}
    if isinstance(chat_session.collected_data, dict):
        snapshot_raw = chat_session.collected_data.get(
            crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY
        )
    source = snapshot_raw or org_config_dict.get("field_definitions") or org_config_dict.get(
        "required_fields"
    )
    return order_field_definitions(parse_field_definitions(source))


def _allows_optional_contact_fields(org_config_dict: Dict[str, Any]) -> bool:
    custom_prompts = org_config_dict.get("custom_prompts")
    if not isinstance(custom_prompts, dict):
        return False
    return custom_prompts.get("allow_optional_contact_fields") is True


def _enforce_runtime_contact_requirements(
    follow_up_fields: list[str],
    collected_data: Dict[str, Any],
    org_config_dict: Dict[str, Any],
) -> list[str]:
    if _allows_optional_contact_fields(org_config_dict):
        return follow_up_fields
    if follow_up_fields:
        return follow_up_fields
    required = list(follow_up_fields)
    configured_source = (
        org_config_dict.get("field_definitions") or org_config_dict.get("required_fields")
    )
    configured_field_definitions = order_field_definitions(
        parse_field_definitions(configured_source)
    )
    required_contacts = {
        field_definition.name
        for field_definition in configured_field_definitions
        if field_definition.required and field_definition.name in {"email", "phone"}
    }
    for field in ("email", "phone"):
        if field not in required_contacts:
            continue
        value = collected_data.get(field)
        has_value = bool(str(value).strip()) if isinstance(value, str) else value is not None
        if not has_value and field not in required:
            required.insert(0, field)
    return required


def _looks_like_confirmation_claim(message: str) -> bool:
    return bool(CONFIRMATION_CLAIM_PATTERN.search(message or ""))


def _is_non_terminal_directive(directive: str) -> bool:
    return directive in {
        "TEXT_INPUT",
        "REQUEST_CONFIRMATION",
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_PHONE_INPUT",
        "REQUIRE_EMAIL_INPUT",
        "REQUIRE_SELECT",
        "REQUIRE_TEXTAREA",
        "REQUIRE_NUMBER_INPUT",
        "REQUIRE_OTP_MODAL",
    }


def _guard_user_visible_booking_message(
    *,
    assistant_message: str,
    directive: str,
    require_otp: bool,
    chat_session: ChatSessionState,
    field_meta: Dict[str, Any],
    fd_map: Dict[str, FieldDefinition],
) -> str:
    if directive == "BOOKING_CONFIRMED_CARD":
        return assistant_message
    if not _looks_like_confirmation_claim(assistant_message):
        return assistant_message

    if directive in {
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_PHONE_INPUT",
        "REQUIRE_EMAIL_INPUT",
        "REQUIRE_SELECT",
        "REQUIRE_TEXTAREA",
        "REQUIRE_NUMBER_INPUT",
    }:
        return message_processor._build_context_aware_response(
            directive=directive,
            field_meta=field_meta,
            collected_data=dict(chat_session.collected_data or {}),
            valid_data={},
            errors={},
            llm_generated_msg=assistant_message,
        )

    if require_otp and not chat_session.otp_verified:
        email = resolve_email_from_collected_data(
            chat_session.collected_data,
            list(fd_map.values()) if isinstance(fd_map, dict) else None,
        )
        if email:
            return (
                f"Before I can confirm your booking, please verify the OTP sent to {email}."
            )
        return "Before I can confirm your booking, please verify your OTP code."

    if directive == "REQUEST_CONFIRMATION":
        summary_message, _ = message_processor._build_confirmation_summary(chat_session, fd_map)
        return summary_message

    if _is_non_terminal_directive(directive):
        return message_processor._build_context_aware_response(
            directive=directive,
            field_meta=field_meta,
            collected_data=dict(chat_session.collected_data or {}),
            valid_data={},
            errors={},
            llm_generated_msg=assistant_message,
        )

    return "I still need to complete your booking before I can confirm it."

async def process_message_with_graph(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
    recovery_metadata: Optional[Dict[str, Any]] = None,
) -> ActionPayload:
    """
    Entry point for the LangGraph-based AI agent.
    Replaces the legacy process_message state machine.
    """
    session_id = chat_session.session_id
    existing_summary, existing_window = _load_runtime_context(chat_session)

    # Fast-path: explicit OTP resend while awaiting verification.
    # The LLM is unreliable at routing a bare "resend", so handle it directly.
    if _is_resend_request(message_text) and _is_awaiting_otp(chat_session):
        return await _handle_graph_otp_resend(
            db, org, chat_session, message_text, background_tasks
        )

    # Convert org_config to a clean dictionary removing SQLAlchemy's internal state
    org_config_dict = {k: v for k, v in org_config.__dict__.items() if not k.startswith('_')}
    # Use business_name for RAG filtering (falls back to org.name if not set)
    org_config_dict["business_name"] = org_config_dict.get("business_name") or org.name

    # 1. Prepare configuration for LangGraph (passed as configurable dict)
    config = {
        "configurable": {
            "thread_id": session_id,
            "session_id": session_id,
            "db": db,
            "org_id": str(org.id),
            "org_config": org_config_dict,
            # Optional: pass user's timezone if captured
            "user_offset_hours": 0 
        }
    }
    
    # 2. Invoke the graph with the new user message
    # If using memory saver, the checkpoint handles previous messages.
    logger.info(f"Invoking LangGraph for session {session_id}")
    prior_missing_fields = list(chat_session.missing_fields or [])
    inputs = {
        "messages": ([_window_item_to_message(item) for item in existing_window] + [HumanMessage(content=message_text)])[-MAX_RUNTIME_CONTEXT_MESSAGES:],
        "summary": existing_summary,
        "session_id": session_id,
        "org_id": str(org.id),
        "org_config": org_config_dict,
        "collected_fields": _normalize_collected_data(chat_session.collected_data),
        "current_intent": str(chat_session.current_intent or "START_BOOKING"),
        "otp_verified": bool(chat_session.otp_verified),
        "optional_skips": [],
        "pending_conflict": {},
        "follow_up_fields": list(chat_session.missing_fields or []),
        "captured_deltas": {},
        "merge_diagnostics": {},
    }
    
    # The agent runs asynchronously across its nodes.
    # We use await ainvoke because our tool checks async slot_engine
    result = await booking_agent.ainvoke(inputs, config=config)

    # Extract the final AI message from the agent's updated state
    messages = result.get("messages", [])
    
    if not messages:
        action = ActionPayload(
            message="I'm sorry, I encountered an internal error. Please try again.",
            widget_directive="TEXT_INPUT"
        )
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=message_text,
            assistant_message=action.message,
            directive=action.widget_directive or "TEXT_INPUT",
            intent=chat_session.current_intent,
        )
        return action
        
    last_message = messages[-1]
    assistant_message, structured_assistant_payload = _extract_user_visible_message(
        getattr(last_message, "content", "")
    )
    
    existing_collected_data = (
        dict(chat_session.collected_data) if isinstance(chat_session.collected_data, dict) else {}
    )
    collected_fields = result.get("collected_fields")
    validated_field_metadata = result.get("validated_field_metadata")
    if isinstance(collected_fields, dict):
        merged_collected_data = dict(existing_collected_data)
        merged_collected_data.update(collected_fields)
        if isinstance(validated_field_metadata, dict):
            phone_meta = validated_field_metadata.get("phone")
            if isinstance(phone_meta, dict):
                country_code = phone_meta.get("country_code")
                raw_input = phone_meta.get("raw_input")
                if isinstance(country_code, str) and country_code.strip():
                    merged_collected_data["phone_country_code"] = country_code.strip().upper()
                if isinstance(raw_input, str) and raw_input.strip():
                    merged_collected_data["phone_raw_input"] = raw_input.strip()
        chat_session.collected_data = merged_collected_data
    follow_up_fields = result.get("follow_up_fields")
    if result.get("slot_unavailable"):
        existing_follow_ups = list(follow_up_fields) if isinstance(follow_up_fields, list) else []
        refreshed_slots = result.get("refreshed_slots")
        has_refreshed_slots = isinstance(refreshed_slots, list) and len(refreshed_slots) > 0
        fallback_reason = str(result.get("fallback_reason") or "").strip() or "no_slots_available_for_date"
        fallback_next_step = str(result.get("fallback_next_step") or "").strip() or "pick_another_date"

        if has_refreshed_slots:
            if "time" not in existing_follow_ups:
                existing_follow_ups.insert(0, "time")
        else:
            for stale_key in ("selected_slot_id", "selected_slot_start_utc", "time", "date"):
                if isinstance(chat_session.collected_data, dict):
                    chat_session.collected_data.pop(stale_key, None)
            existing_follow_ups = ["date"]
            result["date_reset_required"] = True
            result["fallback_reason"] = fallback_reason
            result["fallback_next_step"] = fallback_next_step

        follow_up_fields = existing_follow_ups
    if isinstance(follow_up_fields, list):
        follow_up_fields = _enforce_runtime_contact_requirements(
            follow_up_fields,
            dict(chat_session.collected_data or {}),
            org_config_dict,
        )
    current_intent = str(
        result.get("current_intent") or chat_session.current_intent or "START_BOOKING"
    )
    if isinstance(follow_up_fields, list) and not follow_up_fields:
        user_msg_lower = message_text.lower().strip()
        if message_processor._is_edit_details_request(user_msg_lower):
            current_intent = "EDIT_DATA"
        elif (
            current_intent != "CONFIRM_BOOKING"
            and any(keyword in user_msg_lower for keyword in message_processor.CONFIRM_KEYWORDS)
        ):
            current_intent = "CONFIRM_BOOKING"
    chat_session.current_intent = current_intent

    if isinstance(follow_up_fields, list):
        chat_session.missing_fields = follow_up_fields

    manual_time_requires_slot_selection = (
        "time" in prior_missing_fields
        and "time" not in (chat_session.missing_fields or [])
        and not str(chat_session.collected_data.get("selected_slot_id") or "").strip()
    )
    if manual_time_requires_slot_selection:
        chat_session.collected_data.pop("time", None)
        chat_session.collected_data.pop("locked_slot_id", None)
        chat_session.collected_data.pop("locked_slot_start_utc", None)
        chat_session.missing_fields = [
            "time",
            *[field for field in (chat_session.missing_fields or []) if field != "time"],
        ]
        follow_up_fields = list(chat_session.missing_fields)

    await crud_chat.update_chat_session(db, session_id, chat_session)
    session_state_dirty = False

    async def _write_checkpoint(sid: str, checkpoint: Dict[str, Any]) -> None:
        await crud_chat.save_session_checkpoint(db, sid, checkpoint)

    checkpoint_manager = CheckpointManager(write_checkpoint=_write_checkpoint)
    context_window = _normalize_context_window(result.get("context_window"))
    summary = str(result.get("summary", "") or "")
    checkpoint_state: Dict[str, Any] = {
        "collected_data": dict(chat_session.collected_data or {}),
        "missing_fields": list(chat_session.missing_fields or []),
        "current_intent": chat_session.current_intent,
        "session_status": chat_session.session_status,
        "otp_verified": chat_session.otp_verified,
        "pending_cancel": chat_session.pending_cancel,
        "summary": summary,
        "context_window": context_window,
        "context_window_size": len(context_window),
    }
    captured_deltas = result.get("captured_deltas", {})
    event_type = "field_capture" if captured_deltas else "confirmation"
    await checkpoint_manager.record_event(session_id, event_type, checkpoint_state)

    field_meta = {
        "captured_deltas": captured_deltas,
        "follow_up_fields": follow_up_fields if isinstance(follow_up_fields, list) else [],
        "merge_diagnostics": result.get("merge_diagnostics", {}),
        "context": {
            "window_size": len(context_window),
            "summary_present": bool(summary),
        },
    }
    if manual_time_requires_slot_selection:
        field_meta["validation_error"] = "Please choose a time using the slot buttons below."
        field_meta["directive_retry"] = "REQUIRE_TIME_SLOT_GRID"
    if structured_assistant_payload:
        field_meta["llm_structured_response"] = {
            key: value
            for key, value in structured_assistant_payload.items()
            if key != "response_text"
        }
    if recovery_metadata:
        field_meta["recovery"] = recovery_metadata

    field_definitions = _resolve_field_definitions_snapshot(chat_session, org_config_dict)
    fd_map = {fd.name: fd for fd in field_definitions}
    next_action = StateEvaluator.determine_next_action(
        field_meta.get("follow_up_fields", []),
        current_intent,
        field_definitions=field_definitions,
    )
    directive = str(next_action.get("directive") or "TEXT_INPUT")
    directive_meta = next_action.get("field_meta")
    is_valid_directive, validation_error = StateEvaluator.validate_directive_payload(
        directive,
        directive_meta,
    )

    if not is_valid_directive:
        logger.warning(
            "directive_metadata_invalid",
            extra={
                "session_id": session_id,
                "directive": directive,
                "reason": validation_error,
            },
        )
        directive = "TEXT_INPUT"
        field_meta["directive_fallback"] = "TEXT_INPUT"
        field_meta["directive_validation_error"] = validation_error
    elif isinstance(directive_meta, dict):
        field_meta.update(directive_meta)

    validation_errors = result.get("validation_errors")
    if isinstance(validation_errors, dict):
        if isinstance(follow_up_fields, list):
            for core_field in ("phone", "email"):
                if (
                    isinstance(validation_errors.get(core_field), str)
                    and validation_errors.get(core_field, "").strip()
                    and core_field not in follow_up_fields
                ):
                    follow_up_fields.insert(0, core_field)
            chat_session.missing_fields = follow_up_fields
        active_field = str(field_meta.get("name") or "").strip()
        active_error = validation_errors.get(active_field)
        if isinstance(active_error, str) and active_error.strip():
            field_meta["validation_error"] = active_error.strip()
            if directive in {"REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"}:
                field_meta["directive_retry"] = directive
            else:
                field_meta["directive_retry"] = True

    date_reset_required = bool(result.get("date_reset_required"))
    if date_reset_required:
        for stale_key in ("selected_slot_id", "selected_slot_start_utc", "time", "date"):
            if isinstance(chat_session.collected_data, dict):
                chat_session.collected_data.pop(stale_key, None)
        follow_up_fields = ["date"]
        chat_session.missing_fields = ["date"]
        session_state_dirty = True
        directive = "REQUIRE_DATE_PICKER"
        field_meta.update(
            {
                "name": "date",
                "type": "date",
                "label": "Preferred Date",
                "directive_retry": "REQUIRE_DATE_PICKER",
                "follow_up_fields": ["date"],
                "fallback_reason": str(result.get("fallback_reason") or "invalid_date_input"),
                "fallback_next_step": str(result.get("fallback_next_step") or "pick_another_date"),
                "date_reset_required": True,
            }
        )

    if result.get("slot_unavailable"):
        refreshed_slots = result.get("refreshed_slots")
        options = refreshed_slots if isinstance(refreshed_slots, list) else []
        if options and not date_reset_required:
            directive = "REQUIRE_TIME_SLOT_GRID"
            field_meta.update(
                {
                    "name": "time",
                    "type": "time",
                    "label": "Preferred Time",
                    "validation_error": "That slot is no longer available. Please choose another time.",
                    "directive_retry": "REQUIRE_TIME_SLOT_GRID",
                    "slot_unavailable": True,
                    "options": options,
                }
            )
        elif not date_reset_required:
            directive = "REQUIRE_DATE_PICKER"
            follow_up_fields = ["date"]
            chat_session.missing_fields = ["date"]
            session_state_dirty = True
            field_meta.update(
                {
                    "name": "date",
                    "type": "date",
                    "label": "Preferred Date",
                    "validation_error": "No slots are currently available for that date. Please pick another date.",
                    "directive_retry": "REQUIRE_DATE_PICKER",
                    "slot_unavailable": True,
                    "follow_up_fields": ["date"],
                    "fallback_reason": str(result.get("fallback_reason") or "no_slots_available_for_date"),
                    "fallback_next_step": str(result.get("fallback_next_step") or "pick_another_date"),
                    "date_reset_required": True,
                }
            )

    booking_field_meta: Optional[Dict[str, Any]] = None
    raw_require_otp = getattr(org_config, "require_otp", True)
    require_otp = True if raw_require_otp is None else bool(raw_require_otp)
    otp_required = (
        require_otp
        and result.get("otp_required") is True
        and not chat_session.otp_verified
    )
    otp_event_key = str(
        result.get("otp_event_key")
        or f"otp:{session_id}:{chat_session.collected_data.get('email', '')}"
    )
    if otp_required:
        effect_state = _active_effect_state(chat_session)
        sent_keys = effect_state.get("otp_sent_keys")
        if not isinstance(sent_keys, list):
            sent_keys = []
            effect_state["otp_sent_keys"] = sent_keys

        otp_error: Optional[str] = None
        recipient_email = resolve_email_from_collected_data(
            chat_session.collected_data,
            list(fd_map.values()),
        )
        if otp_event_key not in sent_keys:
            try:
                otp_code = await otp_crud.generate_otp(
                    db,
                    chat_session.session_id,
                    resend=False,
                )
                if recipient_email:
                    if background_tasks:
                        background_tasks.add_task(
                            mailer.send_otp_email,
                            recipient_email,
                            otp_code,
                            org_id=org.id,
                            idempotency_key=otp_event_key,
                        )
                    else:
                        await mailer.send_otp_email(
                            recipient_email,
                            otp_code,
                            org_id=org.id,
                            idempotency_key=otp_event_key,
                        )
                sent_keys.append(otp_event_key)
                await crud_chat.update_chat_session(db, session_id, chat_session)
            except Exception as otp_send_error:
                otp_error = str(otp_send_error)

        try:
            otp_policy_meta = await otp_crud.get_otp_policy_metadata(
                db, chat_session.session_id
            )
        except Exception:
            otp_policy_meta = {}

        directive = "REQUIRE_OTP_MODAL"
        field_meta.update(otp_policy_meta)
        field_meta["otp_required"] = True
        field_meta["otp_event_key"] = otp_event_key
        if otp_error:
            field_meta["validation_error"] = otp_error
        if recipient_email:
            assistant_message = (
                f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. "
                "Please enter it here to continue."
            )
        else:
            assistant_message = "Please verify your OTP code to continue."
    elif directive == "REQUEST_CONFIRMATION":
        captured_deltas = field_meta.get("captured_deltas") if isinstance(field_meta, dict) else {}
        has_captured_changes = isinstance(captured_deltas, dict) and bool(captured_deltas)
        if current_intent == "EDIT_DATA" and not has_captured_changes:
            directive = "TEXT_INPUT"
            field_meta.update(
                {
                    "name": "edit_details",
                    "type": "text",
                    "label": "Update Details",
                    "placeholder": "Tell me what you want to change (name, email, phone, date, or time).",
                }
            )
            assistant_message = "Sure - tell me what you'd like to change (name, email, phone, date, or time)."
        else:
            assistant_message, directive = message_processor._build_confirmation_summary(
                chat_session, fd_map
            )
    elif directive == "PROCESS_BOOKING":
        (
            assistant_message,
            directive,
            booking_field_meta,
        ) = await message_processor._handle_booking_flow(
            db,
            org,
            org_config,
            chat_session,
            current_intent,
            require_otp,
            fd_map,
            background_tasks,
            dispatch_notifications=False,
        )
        if isinstance(booking_field_meta, dict):
            field_meta.update(booking_field_meta)

    if directive == "BOOKING_CONFIRMED_CARD" and chat_session.session_status != "CONFIRMED":
        logger.warning(
            "booking_confirmation_blocked_missing_persistence",
            extra={
                "session_id": session_id,
                "session_status": chat_session.session_status,
            },
        )
        assistant_message, directive = message_processor._build_confirmation_summary(
            chat_session, fd_map
        )

    booking_confirmed = (
        directive == "BOOKING_CONFIRMED_CARD"
        and str(chat_session.session_status).upper() == "CONFIRMED"
    )
    if booking_confirmed:
        confirmation_event_key = _build_confirmation_event_key(
            session_id,
            dict(chat_session.collected_data or {}),
        )
        field_meta["booking_confirmed"] = True
        field_meta["confirmation_event_key"] = confirmation_event_key
        field_meta["confirmation_provenance"] = "orchestrator_commit_outcome"

        effect_state = _active_effect_state(chat_session)
        sent_confirmations = effect_state.get("confirmation_sent_keys")
        if not isinstance(sent_confirmations, list):
            sent_confirmations = []
            effect_state["confirmation_sent_keys"] = sent_confirmations

        if confirmation_event_key not in sent_confirmations:
            booking_details = dict(chat_session.collected_data or {})
            customer_email = booking_details.get("email")
            admin_email = booking_details.get("admin_email")
            if customer_email:
                try:
                    if background_tasks:
                        background_tasks.add_task(
                            mailer.send_confirmation_emails,
                            customer_email,
                            admin_email,
                            booking_details,
                            org_id=org.id,
                        )
                    else:
                        await mailer.send_confirmation_emails(
                            customer_email,
                            admin_email,
                            booking_details,
                            org_id=org.id,
                        )
                except Exception as confirmation_error:
                    logger.error(
                        "confirmation_dispatch_failed",
                        extra={
                            "session_id": session_id,
                            "error": str(confirmation_error),
                        },
                    )
                    field_meta["email_retrying"] = True
                sent_confirmations.append(confirmation_event_key)
                await crud_chat.update_chat_session(db, session_id, chat_session)

    if directive == "REQUIRE_OTP_MODAL":
        field_meta.setdefault("otp_cooldown_seconds", OTP_COOLDOWN_SECONDS)
        field_meta.setdefault("resend_cap", OTP_RESEND_CAP)
        field_meta.setdefault("valid_for_seconds", OTP_VALID_FOR_SECONDS)
        field_meta.setdefault("resends_left", OTP_RESEND_CAP)

    if isinstance(result.get("notification_status"), dict):
        notification_status = result.get("notification_status") or {}
        if notification_status.get("email_retrying") is True:
            field_meta["email_retrying"] = True
    if result.get("email_retrying") is True:
        field_meta["email_retrying"] = True

    # Populate available time slots when the widget needs the time-slot grid.
    # The graph sets the REQUIRE_TIME_SLOT_GRID directive but does not compute
    # slot options itself, so we fetch them here (unless already provided by a
    # slot_unavailable retry above).
    if directive == "REQUIRE_TIME_SLOT_GRID" and not field_meta.get("options"):
        target_date = chat_session.collected_data.get("date")
        if target_date:
            try:
                from dateutil.parser import parse as date_parse
                from app.engine.slot_engine import (
                    compute_slot_inventory,
                    format_slot_time,
                    apply_scheduling_policy_to_inventory,
                )
                from app.engine.scheduling_policy import (
                    resolve_effective_timezone_policy,
                )

                schedule_result = await db.execute(
                    select(BusinessSchedule).where(
                        BusinessSchedule.org_id == org.id
                    )
                )
                schedule = schedule_result.scalars().first()
                org_timezone = str(
                    (schedule.timezone if schedule and schedule.timezone else None)
                    or getattr(org_config, "timezone", None)
                    or "UTC"
                )

                parsed_date = date_parse(target_date).date()
                inventory, availability_status = await compute_slot_inventory(
                    db, org.id, parsed_date, org_config=org_config
                )
                timezone_resolution = resolve_effective_timezone_policy(
                    requested_tz=chat_session.collected_data.get("user_timezone"),
                    org_tz=org_timezone,
                    requested_source=chat_session.collected_data.get(
                        "timezone_source"
                    ),
                )
                inventory, availability_status, policy_reason_code = (
                    apply_scheduling_policy_to_inventory(
                        inventory,
                        parsed_date,
                        base_status=availability_status,
                        effective_timezone=str(
                            timezone_resolution["effective_timezone"]
                        ),
                        allow_same_day_booking=bool(
                            getattr(org_config, "allow_same_day_booking", False)
                        ),
                        slot_cutoff_minutes=getattr(
                            org_config, "slot_cutoff_minutes", 5
                        ),
                    )
                )
                options = [
                    format_slot_time(item["start_utc"].time())
                    for item in inventory
                    if item["available"]
                ]
                field_meta["options"] = options
                field_meta["availability_status"] = availability_status
                if policy_reason_code:
                    field_meta["policy_reason_code"] = policy_reason_code
                logger.info(
                    "computed_time_slots",
                    extra={
                        "session_id": session_id,
                        "target_date": str(target_date),
                        "slot_count": len(options),
                        "availability_status": availability_status,
                    },
                )

                # No slots available: fall back to the date picker instead of
                # asking for a time that does not exist.
                if not options:
                    directive = "REQUIRE_DATE_PICKER"
                    chat_session.collected_data.pop("date", None)
                    chat_session.collected_data.pop("time", None)
                    chat_session.collected_data.pop("selected_slot_id", None)
                    chat_session.missing_fields = ["date"] + [
                        f
                        for f in (chat_session.missing_fields or [])
                        if f != "date"
                    ]
                    await crud_chat.update_chat_session(
                        db, session_id, chat_session
                    )
                    field_meta.update(
                        {
                            "name": "date",
                            "type": "date",
                            "label": "Preferred Date",
                            "directive_retry": "REQUIRE_DATE_PICKER",
                            "follow_up_fields": ["date"],
                        }
                    )
                    if availability_status == "same_day_disabled":
                        assistant_message = (
                            "Same-day booking isn't available for this business. "
                            "Please choose a future date."
                        )
                    else:
                        assistant_message = (
                            "There are no available time slots for that date. "
                            "Please choose another date."
                        )
            except Exception as slot_err:
                logger.warning(
                    "compute_time_slots_failed",
                    extra={"session_id": session_id, "error": str(slot_err)},
                )
                directive = "REQUIRE_DATE_PICKER"
                field_meta["options"] = []
                field_meta["availability_status"] = "no_schedule"
                field_meta.update(
                    {
                        "name": "date",
                        "type": "date",
                        "label": "Preferred Date",
                        "directive_retry": "REQUIRE_DATE_PICKER",
                        "follow_up_fields": ["date"],
                    }
                )
                assistant_message = (
                    "I couldn't load the available times right now. "
                    "Please pick a date and I'll try again."
                )

    assistant_message = _guard_user_visible_booking_message(
        assistant_message=assistant_message,
        directive=directive,
        require_otp=require_otp,
        chat_session=chat_session,
        field_meta=field_meta,
        fd_map=fd_map,
    )
    if session_state_dirty:
        await crud_chat.update_chat_session(db, session_id, chat_session)

    # 3. Create ActionPayload to emulate traditional response
    action = ActionPayload(
        message=assistant_message,
        widget_directive=directive,
        field_meta=field_meta,
        is_terminal=False
    )
    await _persist_transcript_best_effort(
        db,
        session_id=session_id,
        user_message=message_text,
        assistant_message=action.message,
        directive=action.widget_directive or "TEXT_INPUT",
        intent=chat_session.current_intent,
    )
    return action
