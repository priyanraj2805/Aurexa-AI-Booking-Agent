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
from app.engine import cancel_flow
from app.engine.recipient_resolution import resolve_email_from_collected_data
from app.engine.user_visible_text import (
    extract_user_visible_text,
    looks_like_confirmation_claim,
    looks_like_field_saved_claim,
)
from app.schemas.agent import FieldDefinition, parse_field_definitions, order_field_definitions

logger = logging.getLogger(__name__)
MAX_RUNTIME_CONTEXT_MESSAGES = 6
OTP_COOLDOWN_SECONDS = 30
OTP_RESEND_CAP = 3
OTP_VALID_FOR_SECONDS = 300


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
    # Check both email and phone OTP pending — WhatsApp uses phone channel
    if getattr(chat_session, "otp_verified", False) and getattr(chat_session, "phone_otp_verified", False):
        return False
    # If either email or phone OTP is pending, consider awaiting
    # For phone-only orgs (Priyanshu), phone_otp_verified is the gate
    try:
        from app.crud.otp import _get_policy_state as _get_ps
        # If any OTP policy state has been set, we are awaiting
        for ch in ("email", "phone"):
            ps = _get_ps(chat_session, ch) if hasattr(chat_session, "state_data") else {}
            if isinstance(ps, dict) and ps.get("last_sent_at"):
                return True
    except Exception:
        pass
    effect_state = _active_effect_state(chat_session)
    for key in ("otp_sent_keys", "phone_otp_sent_keys", "otp_keys", "phone_otp_keys"):
        sent_keys = effect_state.get(key)
        if isinstance(sent_keys, list) and len(sent_keys) > 0:
            return True
    # Fallback: if phone or email verification is pending and OTP was recently sent (state_data has policy)
    try:
        sd = getattr(chat_session, "state_data", None) or getattr(chat_session, "collected_data", None) or {}
        if isinstance(sd, dict):
            for k in ("_otp_policy", "_otp_phone_policy", "otp_sent_keys", "phone_otp_sent_keys"):
                v = sd.get(k)
                if isinstance(v, dict) and v.get("last_sent_at"):
                    return True
                if isinstance(v, list) and len(v) > 0:
                    return True
    except Exception:
        pass
    # Legacy fallback
    sent_keys = effect_state.get("otp_sent_keys")
    return isinstance(sent_keys, list) and len(sent_keys) > 0


async def _handle_graph_otp_resend(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    # Determine pending channel from org config + verification state — never fallback to disabled channel
    # WhatsApp-only: phone if pending, else email if pending, else error
    try:
        from app.models.config import OrgConfig as _OC
        # org_config may be passed via chat_session? Try to get from org
        # Use get_otp_policy_metadata to infer, but simpler: read org_config if available via db
        # For whatsapp, phone is preferred when both pending; for web, email first (handled in message_processor)
        # Here we need org_config — try to fetch if not in closure
        _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
        # Attempt to get org_config from DB if not provided
        _org_config = None
        try:
            from sqlalchemy.future import select as _sel
            from app.models.config import OrgConfig
            _res = await db.execute(_sel(OrgConfig).where(OrgConfig.org_id == org.id))
            _org_config = _res.scalars().first()
        except Exception:
            _org_config = None
        if _org_config is not None:
            _req_email = bool(getattr(_org_config, "require_otp", False))
            _req_phone = bool(getattr(_org_config, "require_phone_otp", False))
            _phone_pending = _req_phone and not bool(getattr(chat_session, "phone_otp_verified", False))
            _email_pending = _req_email and not bool(getattr(chat_session, "otp_verified", False))
            if _phone_pending and _email_pending:
                _pending = "phone" if _is_whatsapp else "email"
            elif _phone_pending:
                _pending = "phone"
            elif _email_pending:
                _pending = "email"
            else:
                return ActionPayload(
                    message="OTP verification is not required at this time.",
                    widget_directive="TEXT_INPUT",
                )
            # If pending is email but email disabled (should not happen due to above), guard
            if _pending == "email" and not _req_email:
                return ActionPayload(
                    message="Email OTP is disabled for this organization. Please contact support.",
                    widget_directive="TEXT_INPUT",
                )
            if _pending == "phone" and not _req_phone:
                return ActionPayload(
                    message="Phone OTP is disabled for this organization.",
                    widget_directive="TEXT_INPUT",
                )
            return await message_processor._handle_otp_resend(
                db, org, chat_session, message_text, background_tasks, channel=_pending
            )
    except Exception:
        pass
    # Fallback: let message_processor decide (defaults to email, but will now respect pending)
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
        "REQUIRE_CANCEL_LOOKUP",
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

    if directive == "CANCELLED_CONFIRMATION_CARD":
        return assistant_message

    active_field = str((field_meta or {}).get("name") or "").strip()
    invalid_error = ""
    if active_field:
        merge_diag = (field_meta or {}).get("merge_diagnostics")
        if isinstance(merge_diag, dict):
            invalid_fields = merge_diag.get("invalid_fields")
            if isinstance(invalid_fields, dict) and active_field in invalid_fields:
                invalid_error = str(invalid_fields.get(active_field) or "")
        if not invalid_error:
            validation_error = (field_meta or {}).get("validation_error")
            if isinstance(validation_error, str):
                invalid_error = validation_error.strip()
    if (
        invalid_error
        and looks_like_field_saved_claim(assistant_message, active_field)
    ):
        label = str(
            (field_meta or {}).get("label") or active_field.replace("_", " ")
        ).lower()
        return f"I couldn't save that {label}. {invalid_error}"

    if not looks_like_confirmation_claim(assistant_message):
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

_POST_BOOKING_ACTION_THRESHOLD = 0.70


async def _llm_post_booking_analysis(
    message_text: str, status: str, pending_cancel: bool, collected_data: Dict[str, Any]
) -> Dict[str, Any]:
    """LLM-based analysis of a message on a finished session. The model
    classifies intent AND writes the conversational reply in one call —
    no hardcoded phrase matching."""
    import json as _json

    prompt = f"""Analyze the user message in a booking support conversation.

SESSION_STATUS: {status}
PENDING_CANCEL_CONFIRMATION: {str(pending_cancel).lower()}
BOOKING_DETAILS: {_json.dumps(collected_data or {}, default=str)}

USER_MESSAGE: "{message_text}"

Classify the user's intent. Use ONLY the message's actual meaning:

- classification "ACTION_REQUEST": the user is clearly stating they want to
  perform cancel/reschedule/rebook RIGHT NOW. Examples: "cancel my booking",
  "I want to cancel my booking", "please cancel it", "reschedule",
  "I need to reschedule", "book again".
- classification "INFO_QUESTION": the user only asks HOW/WHETHER something is
  possible, not that they want it done now. Examples: "how do I cancel?",
  "can I change the time?".
- classification "CONFIRM": user confirms a previously asked action.
- classification "DENY": user declines a previously asked action.
- classification "ACKNOWLEDGMENT": thanks/ok/sure/acknowledgment, not a request.
- classification "NEUTRAL": anything else.

action is "CANCEL", "RESCHEDULE", or "REBOOK" ONLY for ACTION_REQUEST;
otherwise "NONE".

IMPORTANT: A user who says "I want to cancel my booking" (or similar direct
request) IS making an ACTION_REQUEST — do NOT treat it as an info question and
do NOT reply asking whether they want to proceed.

If no action will be executed, write "reply": a warm 1-2 sentence assistant
reply that is accurate for the situation (booking confirmed/cancelled/expired),
answers the user's question if any, and mentions they can say "cancel my
booking" / "reschedule" when the booking is confirmed.

Return ONLY valid JSON:
{{"classification": "ACTION_REQUEST|INFO_QUESTION|CONFIRM|DENY|ACKNOWLEDGMENT|NEUTRAL",
  "action": "CANCEL|RESCHEDULE|REBOOK|NONE",
  "confidence": 0.0,
  "reply": "..."}}
"""
    try:
        result = await message_processor.llm_engine._call_llm(
            prompt,
            system_prompt="You are a strict booking-support intent classifier. Output JSON only.",
            json_mode=True,
        )
        payload = _json.loads(str(result or ""))
        classification = str(payload.get("classification", "NEUTRAL")).upper()
        action = str(payload.get("action", "NONE")).upper()
        confidence = float(payload.get("confidence", 0.0))
        reply = str(payload.get("reply") or "").strip()
        return {
            "classification": classification,
            "action": action,
            "confidence": confidence,
            "reply": reply,
        }
    except Exception:
        return {
            "classification": "NEUTRAL",
            "action": "NONE",
            "confidence": 0.0,
            "reply": "",
        }


async def _handle_post_booking_session(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """
    Handling for messages on finished sessions (CONFIRMED / CANCELLED /
    EXPIRED). The LLM analyzes each message automatically (intent + reply)
    and the result is dispatched deterministically — no hardcoded phrases,
    and the booking flow is never re-entered.
    """
    status = chat_session.session_status

    # LLM analysis of the user's actual message.
    analysis = await _llm_post_booking_analysis(
        message_text=message_text,
        status=status,
        pending_cancel=bool(chat_session.pending_cancel),
        collected_data=dict(chat_session.collected_data or {}),
    )
    classification = str(analysis.get("classification") or "NEUTRAL").upper()
    action = str(analysis.get("action") or "NONE").upper()
    confidence = float(analysis.get("confidence") or 0.0)
    reply = str(analysis.get("reply") or "").strip()
    is_action_request = (
        confidence >= _POST_BOOKING_ACTION_THRESHOLD
        and action in ("CANCEL", "RESCHEDULE", "REBOOK")
    )

    # 1. Explicit cancel request on a CONFIRMED booking → OTP-protected cancel
    #    flow (lookup → cancel-scoped OTP → execution).
    if status == "CONFIRMED" and is_action_request and action == "CANCEL":
        action_payload = await cancel_flow.start_cancel_flow(db, chat_session, message_text)
        await _persist_transcript_best_effort(
            db,
            session_id=chat_session.session_id,
            user_message=message_text,
            assistant_message=action_payload.message,
            directive=action_payload.widget_directive or "TEXT_INPUT",
            intent="CANCEL_BOOKING",
        )
        return action_payload

    # 2. Explicit reschedule request on a CONFIRMED booking → cancel slot +
    #    reopen flow, keeping name/email/phone (date/time re-collected).
    if status == "CONFIRMED" and is_action_request and action == "RESCHEDULE":
        return await message_processor._reschedule_booking(
            db, org, chat_session, background_tasks
        )

    # 3. Confirmation / denial of a previously asked (legacy) cancellation.
    if status == "CONFIRMED" and chat_session.pending_cancel:
        if classification == "CONFIRM":
            chat_session.pending_cancel = False
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            return await message_processor._cancel_confirmed_booking(
                db, org, chat_session, background_tasks
            )
        if classification == "DENY":
            chat_session.pending_cancel = False
            await crud_chat.update_chat_session(
                db, chat_session.session_id, chat_session
            )
            msg = "No worries — your booking stays as is! Let me know if you need anything else. 😊"
            action_payload = ActionPayload(
                message=msg, widget_directive="TEXT_INPUT", is_terminal=False
            )
            await _persist_transcript_best_effort(
                db,
                session_id=chat_session.session_id,
                user_message=message_text,
                assistant_message=msg,
                directive="TEXT_INPUT",
                intent=chat_session.current_intent,
            )
            return action_payload

    # 4. Rebook from CANCELLED / EXPIRED → fresh booking state.
    if status in ("CANCELLED", "EXPIRED") and action == "REBOOK" and (
        is_action_request or classification in ("CONFIRM", "NEUTRAL")
    ):
        cancel_flow.reset_for_new_booking(chat_session)
        await cancel_flow._persist_session(
            db, chat_session, remove_keys=cancel_flow.SESSION_RESET_REMOVE_KEYS
        )
        try:
            from app.engine.agent.graph import memory as _graph_memory
            await _graph_memory.adelete_thread(chat_session.session_id)
        except Exception as _thread_err:
            logger.warning(
                "graph_thread_reset_failed",
                extra={"session_id": chat_session.session_id, "error": str(_thread_err)},
            )
        msg = "Hello! 👋 I'd be happy to help you book a new appointment. What's your full name?"
        action_payload = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", is_terminal=False
        )
        await _persist_transcript_best_effort(
            db,
            session_id=chat_session.session_id,
            user_message=message_text,
            assistant_message=msg,
            directive="TEXT_INPUT",
            intent="START_BOOKING",
        )
        return action_payload

    # 5. Everything else → the LLM's own status-aware reply. Always TEXT_INPUT;
    #    never a booking-collection widget.
    if not reply:
        reply = {
            "CONFIRMED": "Your booking is confirmed! You can say 'cancel my booking' or 'reschedule' anytime.",
            "CANCELLED": "Your previous booking was cancelled. Just say 'I want to book' to start a new one!",
            "EXPIRED": "This session expired. Say 'I want to book' to start a new booking!",
        }.get(status, "Is there anything else I can help you with?")
    action_payload = ActionPayload(
        message=reply, widget_directive="TEXT_INPUT", is_terminal=False
    )
    await _persist_transcript_best_effort(
        db,
        session_id=chat_session.session_id,
        user_message=message_text,
        assistant_message=reply,
        directive="TEXT_INPUT",
        intent=chat_session.current_intent,
    )
    return action_payload


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

    # Cancellation gate: deterministic pre-graph handling while a cancel flow is
    # active (lookup → OTP → execute). Runs before the resend fast-path below so
    # cancel-context OTP resends never leak into the booking flow.
    cancel_action = await cancel_flow.handle_active_cancel_flow(
        db, org, org_config, chat_session, message_text, background_tasks
    )
    if cancel_action is not None:
        return cancel_action

    # Fast-path: explicit OTP resend while awaiting verification.
    # The LLM is unreliable at routing a bare "resend", so handle it directly.
    if _is_resend_request(message_text) and _is_awaiting_otp(chat_session):
        return await _handle_graph_otp_resend(
            db, org, chat_session, message_text, background_tasks
        )

    # Fast-path: 6-digit OTP code sent as chat message (WhatsApp phone OTP
    # or email OTP). Verify deterministically before the LLM sees it — the
    # LLM hallucinates "no pending" otherwise and the code is lost.
    _stripped = str(message_text or "").strip()
    _is_six = bool(re.fullmatch(r"\d{6}", _stripped))
    _awaiting = _is_awaiting_otp(chat_session)
    _phone_pending_log = bool(getattr(org_config, "require_phone_otp", False) and not bool(getattr(chat_session, "phone_otp_verified", False)))
    logger.info(f"OTP chat check: msg='{_stripped[:20]}' is_6digit={_is_six} awaiting={_awaiting} phone_pending={_phone_pending_log}")
    if _is_six:
        # Determine which OTP channel is actually pending for this org/session.
        _phone_pending = (
            bool(getattr(org_config, "require_phone_otp", False))
            and not bool(getattr(chat_session, "phone_otp_verified", False))
            and bool(str((chat_session.collected_data or {}).get("phone") or "").strip())
        )
        _email_pending = (
            bool(getattr(org_config, "require_otp", True))
            and not bool(getattr(chat_session, "otp_verified", False))
        )
        # Try the more likely channel first: phone for WhatsApp, email otherwise.
        # whatsapp_cloud (Meta Cloud API) is WhatsApp-class: same OTP routing.
        _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
        _channels = []
        if _phone_pending and _email_pending:
            _channels = ["phone", "email"] if _is_whatsapp else ["email", "phone"]
        elif _phone_pending:
            _channels = ["phone"]
        elif _email_pending:
            _channels = ["email"]
        # Only attempt verify if at least one channel is pending and we have
        # a corresponding OTP sent key (prevents treating booking numbers as OTP).
        if _channels and _is_awaiting_otp(chat_session):
            _verified_channel = None
            for _ch in _channels:
                try:
                    if await otp_crud.verify_otp(db, session_id, code=_stripped, channel=_ch):
                        _verified_channel = _ch
                        break
                except ValueError:
                    # Too many attempts / cooldown — surface as invalid
                    pass
                except Exception:
                    pass
            if _verified_channel:
                # Mark verified in session so the graph sees it and proceeds to
                # booking in the same turn. Persist before graph invocation.
                if _verified_channel == "phone":
                    chat_session.phone_otp_verified = True
                    try:
                        _es = _active_effect_state(chat_session)
                        _sk = _es.get("otp_sent_keys")
                        if isinstance(_sk, list):
                            # Clear phone OTP keys; keep email keys if any
                            _es["otp_sent_keys"] = [k for k in _sk if not str(k).startswith("otp_phone:")]
                            if not _es["otp_sent_keys"]:
                                _es.pop("otp_sent_keys", None)
                    except Exception:
                        pass
                else:
                    chat_session.otp_verified = True
                    # Clear the email OTP pending keys so _is_awaiting_otp flips
                    try:
                        _es = _active_effect_state(chat_session)
                        _es.pop("otp_sent_keys", None)
                    except Exception:
                        pass
                await crud_chat.update_chat_session(db, session_id, chat_session)
                # Fall through to graph — with verified flag set, it will
                # proceed to booking confirmation instead of re-asking for OTP.
            else:
                # Invalid / expired code — return deterministically, don't let
                # the LLM invent a booking number or "no pending" hallucination.
                _msg = "That code is incorrect or has expired. Please check the 6-digit code and try again, or say 'resend' to get a new code."
                _field_meta: Dict[str, Any] = {"channel": _channels[0] if _channels else "phone"}
                try:
                    _meta = await otp_crud.get_otp_policy_metadata(db, session_id, channel=_channels[0])
                    _field_meta.update(_meta)
                except Exception:
                    pass
                _action = ActionPayload(message=_msg, widget_directive="REQUIRE_OTP_MODAL", field_meta=_field_meta)
                await _persist_transcript_best_effort(db, session_id=session_id, user_message=message_text, assistant_message=_msg, directive="REQUIRE_OTP_MODAL", intent=chat_session.current_intent)
                return _action

    # Post-booking gate: a finished session (CONFIRMED / CANCELLED / EXPIRED)
    # must NEVER re-enter the booking flow. The graph has no status awareness
    # and would happily re-collect fields (e.g. "sure" after a confirmed
    # booking surfaced a date picker + no-slot fallback). Route post-booking
    # messages deterministically instead.
    if chat_session.session_status in ("CONFIRMED", "CANCELLED", "EXPIRED"):
        return await _handle_post_booking_session(
            db, org, org_config, chat_session, message_text, background_tasks
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

    # Cancellation entry: if the LLM classified (or keywords indicate) a cancel
    # request and no cancel flow is active, branch into the deterministic
    # cancellation flow instead of applying booking state.
    llm_intent = str((structured_assistant_payload or {}).get("user_intent") or "")
    if cancel_flow.is_cancel_intent(message_text, llm_user_intent=llm_intent):
        action = await cancel_flow.start_cancel_flow(db, chat_session, message_text)
        await _persist_transcript_best_effort(
            db,
            session_id=session_id,
            user_message=message_text,
            assistant_message=action.message,
            directive=action.widget_directive or "TEXT_INPUT",
            intent="CANCEL_BOOKING",
        )
        return action
    
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

    # ── Change collected field override (deterministic Python-side) ──
    # When the user asks to change a field that was already collected, re-ask
    # exactly that field instead of emitting the next missing field's widget.
    # Skipped when the same turn already captured a fresh value for the field.
    CHANGE_COLLECTED_FIELD_KEYWORDS = {
        "name": [
            "change name",
            "change my name",
            "update name",
            "wrong name",
        ],
        "email": [
            "change email",
            "change my email",
            "update email",
            "wrong email",
            "different email",
        ],
        "phone": [
            "change phone",
            "change my phone",
            "update phone",
            "wrong phone",
            "change number",
            "change my number",
            "different number",
        ],
        "date": [
            "change date",
            "change the date",
            "change my date",
            "different date",
            "another date",
            "wrong date",
            "pick another date",
            "choose another date",
            "switch date",
            "update date",
            "new date",
        ],
        "time": [
            "change time",
            "change my time",
            "change the time",
            "wrong time",
            "update time",
            "different time",
        ],
    }
    missing_set_override = set(chat_session.missing_fields or [])
    changed_field = None
    for field, kws in CHANGE_COLLECTED_FIELD_KEYWORDS.items():
        if field in missing_set_override:
            continue
        if field not in (chat_session.collected_data or {}):
            continue
        if any(kw in message_text.lower() for kw in kws):
            changed_field = field
            break
    if changed_field:
        chat_session.collected_data.pop(changed_field, None)
        if changed_field in ("date", "time"):
            chat_session.collected_data.pop("selected_slot_id", None)
            chat_session.collected_data.pop("selected_slot_start_utc", None)
            chat_session.collected_data.pop("locked_slot_id", None)
            chat_session.collected_data.pop("locked_slot_start_utc", None)
        if changed_field == "date":
            chat_session.collected_data.pop("time", None)
        follow_up_fields = [changed_field] + [
            f for f in follow_up_fields if f != changed_field
        ]
        chat_session.missing_fields = list(follow_up_fields)

    await crud_chat.update_chat_session(db, session_id, chat_session)
    session_state_dirty = False

    async def _write_checkpoint(sid: str, checkpoint: Dict[str, Any]) -> None:
        await crud_chat.save_session_checkpoint(db, sid, checkpoint)
        if isinstance(chat_session.collected_data, dict):
            chat_session.collected_data[crud_chat.RUNTIME_CHECKPOINT_KEY] = checkpoint

    checkpoint_manager = CheckpointManager(write_checkpoint=_write_checkpoint)
    context_window = _normalize_context_window(result.get("context_window"))
    summary = str(result.get("summary", "") or "")
    # FIX: prevent nested duplication of runtime_checkpoint inside collected_data
    _clean_collected = dict(chat_session.collected_data or {})
    _clean_collected.pop(crud_chat.RUNTIME_CHECKPOINT_KEY, None)
    checkpoint_state: Dict[str, Any] = {
        "collected_data": _clean_collected,
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
    # Hybrid meeting type selection before booking
    _collected = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
    _need_meeting_type = False
    try:
        from app.models.config import MeetingMode as _MM
        from app.engine.message_processor import _normalize_meeting_mode as _normM
        _m = _normM(getattr(org_config, "meeting_mode", None))
        _allow = bool(getattr(org_config, "allow_hybrid_selection", False))
        if not field_meta.get("follow_up_fields") and not _collected.get("meeting_type") and _m == _MM.HYBRID and _allow:
            # Check if user just sent Online/Offline
            _msg_lower = (message_text or "").strip().lower()
            if _msg_lower in ("online", "offline"):
                _collected["meeting_type"] = "ONLINE" if _msg_lower == "online" else "OFFLINE"
                chat_session.collected_data = _collected
                await crud_chat.update_chat_session(db, session_id, chat_session)
            else:
                _need_meeting_type = True
    except Exception:
        pass
    if _need_meeting_type:
        directive = "REQUIRE_SELECT"
        field_meta.update({
            "name": "meeting_type",
            "type": "select",
            "label": "Meeting Type",
            "options": ["Online", "Offline"],
            "description": "How would you like to meet?",
            "icon": "💻",
        })
        next_action = {"directive": directive, "field_meta": dict(field_meta)}
        directive_meta = dict(field_meta)
    else:
        next_action = StateEvaluator.determine_next_action(
            field_meta.get("follow_up_fields", []),
            current_intent,
            field_definitions=field_definitions,
        )
        directive = str(next_action.get("directive") or "TEXT_INPUT")
        directive_meta = next_action.get("field_meta")
    # DIAGNOSTIC for WhatsApp time-slot directive (as requested, no code change beyond logging)
    try:
        _diag_state_data = getattr(chat_session, "collected_data", {}) or {}
        _diag_active = _diag_state_data.get("active_directive_id") if isinstance(_diag_state_data, dict) else None
        # Also check state_data via ChatSessionState mapping
        _diag_missing = getattr(chat_session, "missing_fields", None)
        _diag_date = _diag_state_data.get("date") if isinstance(_diag_state_data, dict) else None
        logger.info(
            f"DIRECTIVE_DIAG _is_whatsapp={str(getattr(chat_session, 'channel', '')).lower() in ('whatsapp', 'whatsapp_cloud')} directive={directive} missing_fields={_diag_missing} "
            f"directive_meta={str(directive_meta)[:300] if directive_meta else None} "
            f"options_count={len(directive_meta.get('options') or []) if isinstance(directive_meta, dict) and directive_meta.get('options') else 0} "
            f"directive_type_map={directive} -> { {'REQUIRE_DATE_PICKER': 'SELECT_DATE', 'REQUIRE_TIME_SLOT_GRID': 'SELECT_SLOT', 'REQUIRE_SELECT': 'SELECT_SLOT', 'SELECT_DROPDOWN': 'SELECT_SLOT', 'REQUEST_CONFIRMATION': 'CONFIRM_BOOKING'}.get(directive, 'UNKNOWN')} "
            f"booking_date={_diag_date} active_directive_id={str(_diag_active)[:8] if _diag_active else None} state_data_keys={list(_diag_state_data.keys())[:6] if isinstance(_diag_state_data, dict) else 'not_dict'}"
        )
    except Exception as _diag_e:
        logger.warning(f"DIRECTIVE_DIAG failed: {_diag_e}")
    # WhatsApp BookingDirective — create persisted directive for structured UI
    # (WhatsApp-only, behind no flag for now as requested — widget keeps its own path)
    # whatsapp_cloud (Meta Cloud API) is WhatsApp-class: same directives/IDs/handler.
    _is_whatsapp = str(getattr(chat_session, "channel", "")).lower() in ("whatsapp", "whatsapp_cloud")
    _directive_created = None
    if _is_whatsapp and directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID", "REQUIRE_SELECT", "SELECT_DROPDOWN", "REQUEST_CONFIRMATION", "BOOKING_SUMMARY_CARD"):
        try:
            from app.engine.booking_directive.service import create_directive as _create_dir
            from app.engine.booking_directive.service import DirectiveType as _DT

            _type_map = {
                "REQUIRE_DATE_PICKER": _DT.SELECT_DATE,
                "REQUIRE_TIME_SLOT_GRID": _DT.SELECT_SLOT,
                "REQUIRE_SELECT": _DT.SELECT_SLOT,
                "SELECT_DROPDOWN": _DT.SELECT_SLOT,
                "REQUEST_CONFIRMATION": _DT.CONFIRM_BOOKING,
                "BOOKING_SUMMARY_CARD": _DT.CONFIRM_BOOKING,
            }
            _dtype = _type_map.get(directive)
            if _dtype:
                _opts = []
                _raw = directive_meta.get("options") if isinstance(directive_meta, dict) else []
                # If raw is empty for SELECT_DATE/SLOT, generate authoritatively so directive is never skipped
                if not _raw and _dtype in (_DT.SELECT_DATE, _DT.SELECT_SLOT):
                    try:
                        if _dtype == _DT.SELECT_DATE:
                            from app.engine.message_processor import (
                                _format_available_dates_for_prompt,
                                _upcoming_date_options,
                            )

                            _tz = str(getattr(org_config, "timezone", None) or "UTC")
                            _max = int(getattr(org_config, "max_booking_days_ahead", 30) or 30)
                            _gen = _upcoming_date_options(_tz, _max)
                            _raw = _gen if _gen else []
                            # attach helpful description with format + availability
                            if _raw and not directive_meta.get("description"):
                                try:
                                    directive_meta["description"] = _format_available_dates_for_prompt(_tz, _max, count=min(7, len(_raw)))
                                except Exception:
                                    pass
                        elif _dtype == _DT.SELECT_SLOT:
                            # Generate real slots for the current booking date
                            _target = None
                            if isinstance(directive_meta, dict):
                                _target = directive_meta.get("target_date") or directive_meta.get("date")
                            if not _target:
                                _target = (chat_session.collected_data or {}).get("date")
                            if _target:
                                from dateutil.parser import parse as _parse
                                from app.engine.slot_engine import compute_slot_inventory, format_slot_time
                                from app.engine.scheduling_policy import resolve_effective_timezone_policy

                                _parsed = _parse(str(_target)).date()
                                _org_cfg = org_config
                                _tz_name = str(getattr(_org_cfg, "timezone", None) or "UTC")
                                _inv, _stat = await compute_slot_inventory(db, org.id, _parsed, org_config=_org_cfg)
                                _tz_res = resolve_effective_timezone_policy(
                                    requested_tz=(chat_session.collected_data or {}).get("user_timezone"),
                                    org_tz=_tz_name,
                                    requested_source=(chat_session.collected_data or {}).get("timezone_source"),
                                )
                                from app.engine.slot_engine import apply_scheduling_policy_to_inventory

                                _inv, _stat, _ = apply_scheduling_policy_to_inventory(
                                    _inv, _parsed, base_status=_stat, effective_timezone=str(_tz_res.get("effective_timezone") or _tz_name), allow_same_day_booking=False, slot_cutoff_minutes=5
                                )
                                try:
                                    from zoneinfo import ZoneInfo
                                except ImportError:
                                    from backports.zoneinfo import ZoneInfo
                                try:
                                    _tz_obj = ZoneInfo(str(_tz_res.get("effective_timezone") or _tz_name))
                                except Exception:
                                    import datetime as _dt

                                    _tz_obj = _dt.timezone.utc
                                _slot_labels = []
                                for _it in _inv:
                                    if not _it.get("available"):
                                        continue
                                    _s = _it.get("start_utc")
                                    if _s is None:
                                        continue
                                    try:
                                        if getattr(_s, "tzinfo", None) is None:
                                            import datetime as _dt2

                                            _s = _s.replace(tzinfo=_dt2.timezone.utc)
                                        _slot_labels.append(format_slot_time(_s.astimezone(_tz_obj).time()))
                                    except Exception:
                                        continue
                                    if len(_slot_labels) >= 10:
                                        break
                                _raw = _slot_labels
                                # Also set time prompt in requested format: hours + duration, with closure/exception
                                if _slot_labels and not directive_meta.get("description"):
                                    try:
                                        from app.engine.message_processor import _format_time_prompt_for_whatsapp
                                        _weekly3 = None
                                        _excs3 = None
                                        try:
                                            from app.models.booking import ScheduleDay as _SD3b, ScheduleException as _SE3b
                                            from sqlalchemy.future import select as _sel3b
                                            _sd3b_res = await db.execute(_sel3b(_SD3b).where(_SD3b.org_id == org.id))
                                            _sd3b = _sd3b_res.scalars().all()
                                            _weekly3 = [
                                                {"day_of_week": r.day_of_week, "is_active": r.is_active, "open_time": r.open_time, "close_time": r.close_time}
                                                for r in _sd3b
                                            ]
                                            _se3b_res = await db.execute(_sel3b(_SE3b).where(_SE3b.org_id == org.id))
                                            _se3b = _se3b_res.scalars().all()
                                            _excs3 = [
                                                {"exception_date": r.exception_date, "exception_type": str(r.exception_type), "custom_open": r.custom_open, "custom_close": r.custom_close}
                                                for r in _se3b
                                            ]
                                        except Exception:
                                            pass
                                        _slot_mins3 = None
                                        try:
                                            from app.models.booking import BusinessSchedule as _BS3c
                                            from sqlalchemy.future import select as _sel3c
                                            _s3c_res = await db.execute(_sel3c(_BS3c).where(_BS3c.org_id == org.id))
                                            _s3c = _s3c_res.scalars().first()
                                            if _s3c:
                                                _slot_mins3 = getattr(_s3c, "slot_duration_minutes", None)
                                        except Exception:
                                            pass
                                        directive_meta["description"] = _format_time_prompt_for_whatsapp(
                                            str(_parsed.date().isoformat()) if '_parsed' in locals() else str(_target or ""),
                                            str(_tz_res.get("effective_timezone") or _tz_name),
                                            _slot_mins3,
                                            weekly_schedule=_weekly3,
                                            exceptions=_excs3,
                                        )
                                    except Exception:
                                        pass
                    except Exception as _e:
                        logger.warning(f"Directive option generation failed for {_dtype}: {_e}", exc_info=True)
                        _raw = []
                for _i, _o in enumerate((_raw or [])[:10]):
                    _label = str(_o) if isinstance(_o, str) else str(_o.get("label") or _o.get("title") or _o)
                    # For SELECT_DATE, value must be ISO
                    _value = _label
                    _oid = f"{_dtype.lower()}_{_i}_{_label.replace(' ', '_')[:20]}"
                    if _dtype == _DT.SELECT_DATE:
                        try:
                            from dateutil.parser import parse as _parse_date

                            _parsed = _parse_date(_label)
                            if _parsed.year < 2000:
                                from datetime import datetime as _dt

                                _now_year = _dt.now().year
                                try:
                                    _parsed = _parse_date(f"{_label} {_now_year}")
                                except Exception:
                                    pass
                            _value = _parsed.date().isoformat()
                            _oid = f"date_{_value.replace('-', '')}"
                        except Exception:
                            _value = _label
                            _oid = f"date_{_label.replace(' ', '_').replace(',', '')[:20]}"
                    elif _dtype == _DT.SELECT_SLOT:
                        # Stable id with date prefix
                        _target_for_id = None
                        if isinstance(directive_meta, dict):
                            _target_for_id = directive_meta.get("target_date") or directive_meta.get("date")
                        if not _target_for_id:
                            _target_for_id = (chat_session.collected_data or {}).get("date") or "slot"
                        _oid = f"slot_{str(_target_for_id).replace('-', '')}_{_label.replace(':', '').replace(' ', '')}"
                        _value = _label
                    _opts.append({"id": _oid, "label": _label, "value": _value})
                if _dtype == _DT.CONFIRM_BOOKING and not _opts:
                    _opts = [{"id": "confirm_booking", "label": "CONFIRM", "value": "confirm"}, {"id": "change_booking", "label": "CHANGE", "value": "change"}]
                if _opts or _dtype == _DT.CONFIRM_BOOKING:
                    # Use stable WhatsApp sender identity (channel_user_id) as customer_id
                    # Do NOT fallback to session_id — booking_directives.customer_id must equal whatsapp from_phone
                    _customer_id = str(getattr(chat_session, "channel_user_id", "") or "").strip()
                    if not _customer_id:
                        # Fallback: try to derive from collected_data phone if channel_user_id not yet set (first turn race)
                        _phone_from_data = str((chat_session.collected_data or {}).get("phone") or "").strip()
                        import re as _re2
                        _digits = _re2.sub(r"\D", "", _phone_from_data)
                        _customer_id = _digits if len(_digits) >= 7 else ""
                    if not _customer_id:
                        logger.warning(f"Directive create skipped: no customer_id for session {str(chat_session.session_id)[:8]} channel={getattr(chat_session, 'channel', '')}")
                    else:
                        # Build rich description: business hours + availability (date) or times (slot)
                        _desc_for_create = ""
                        if isinstance(directive_meta, dict) and directive_meta.get("description"):
                            _desc_for_create = str(directive_meta.get("description") or "")
                        # For SELECT_DATE without description, horizon only (as requested)
                        if not _desc_for_create and _dtype == _DT.SELECT_DATE:
                            try:
                                from app.engine.message_processor import _format_available_dates_for_prompt as _fmt_avail
                                _tz4 = str(getattr(org_config, "timezone", None) or "UTC")
                                _max4 = int(getattr(org_config, "max_booking_days_ahead", 30) or 30)
                                _desc_for_create = _fmt_avail(_tz4, _max4, count=7)
                            except Exception:
                                pass
                        _directive_created = await _create_dir(
                            db,
                            org_id=org.id,
                            customer_id=_customer_id,
                            session_id=chat_session.session_id,
                            type=_dtype,
                            title="Select your preferred date" if _dtype == _DT.SELECT_DATE else "Select your preferred time" if _dtype in (_DT.SELECT_SLOT, "SELECT_TIME") else "Please confirm your booking",
                            description=_desc_for_create,
                            options=_opts,
                            required=True,
                            expires_in_seconds=300 if _dtype in (_DT.SELECT_SLOT, "SELECT_TIME") else 600,
                            meta={"field_name": directive_meta.get("name") if isinstance(directive_meta, dict) else None, "directive": directive, **(directive_meta if isinstance(directive_meta, dict) else {})},
                        )
                        # Persist active_directive_id synchronously before response
                        _cd = dict(chat_session.collected_data or {})
                        _cd["active_directive_id"] = str(_directive_created.id)
                        chat_session.collected_data = _cd
                        await db.flush()
                        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
                        await db.flush()
                        logger.info(f"Directive persisted: id={str(_directive_created.id)[:8]} type={_dtype} customer={_customer_id[-4:]} session={str(chat_session.session_id)[:8]}")
                        # Ensure the rendered reply uses the persisted directive's stable IDs/values
                        if isinstance(directive_meta, dict):
                            directive_meta["options"] = [dict(o) for o in _directive_created.options]
                        if isinstance(field_meta, dict) and isinstance(directive_meta, dict):
                            for _k, _v in directive_meta.items():
                                field_meta[_k] = _v
        except Exception as _e:
            logger.exception(f"Directive create failed: {_e}")
    # WhatsApp parity: date picker needs tappable options just like the widget's
    # calendar. The widget ignores field_meta.options for dates, but WhatsApp
    # renders them as a list — ensure 7 upcoming days are present.
    if directive == "REQUIRE_DATE_PICKER" and isinstance(directive_meta, dict) and not directive_meta.get("options"):
        try:
            from app.engine.message_processor import _upcoming_date_options

            _org_tz = str(getattr(org_config, "timezone", None) or "UTC")
            _max_days = int(getattr(org_config, "max_booking_days_ahead", 90) or 90)
            _opts = _upcoming_date_options(_org_tz, _max_days)
            directive_meta["options"] = _opts
            field_meta.update(directive_meta)
            logger.info(f"DATE_LIST_INJECTED directive={directive} opts={len(_opts)}")
        except Exception as _e:
            logger.warning(f"DATE_LIST_INJECT failed: {_e}")
    elif directive == "REQUIRE_DATE_PICKER":
        logger.info(f"DATE_LIST_SKIP directive={directive} has_options={bool(directive_meta.get('options')) if isinstance(directive_meta, dict) else 'no_meta'}")
    # Proactive hours for WhatsApp parity: when asking for date/time, show
    # a concise hours line contextually as per message flow (no verbose block).
    if directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"):
        try:
            _short = "Hours: Mon-Fri 9:00 AM–5:00 PM, Sat 9:00 AM–4:00 PM, Sun Closed (Asia/Calcutta)"
            try:
                from app.engine.scheduling_policy import load_weekly_hours_block

                _full = await load_weekly_hours_block(db, str(org.id))
                if _full and "Monday" in _full:
                    _first = _full.split("\n")[0].replace("## ", "").replace(" (authoritative — answer timing questions from this)", "")
                    if len(_first) < 120:
                        _short = _first.strip()
            except Exception:
                pass
            if isinstance(assistant_message, str) and "Hours:" not in assistant_message and "business hours" not in assistant_message.lower():
                assistant_message = f"{_short}\n{assistant_message}"
        except Exception:
            pass
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
                ):
                    if core_field in follow_up_fields:
                        follow_up_fields.remove(core_field)
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
    raw_require_phone_otp = getattr(org_config, "require_phone_otp", False)
    require_phone_otp = bool(raw_require_phone_otp)
    otp_required = (
        require_otp
        and result.get("otp_required") is True
        and not chat_session.otp_verified
    )
    # Phone OTP — Zavu sms_oneway (kd78m7agx48rpyegpfrmhp5rsx8dm8t9) BYO-OTP; independent of email gate
    phone_otp_required = (
        require_phone_otp
        and not getattr(chat_session, "phone_otp_verified", False)
        and str(chat_session.collected_data.get("phone") or "").strip() != ""
        and not result.get("follow_up_fields")  # all fields collected
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
        field_meta["channel"] = "email"
        if otp_error:
            field_meta["validation_error"] = otp_error
        if recipient_email:
            assistant_message = (
                f"Excellent! I have all your details. I've sent a verification code to {recipient_email}. "
                "Please enter it here to continue."
            )
        else:
            assistant_message = "Please verify your OTP code to continue."
    elif phone_otp_required:
        # Phone verification to confirm booking — provider abstraction
        # (WhatsApp active, SMS fallback), same as legacy path + /generate.
        phone = str(chat_session.collected_data.get("phone") or "").strip()
        phone_event_key = f"otp_phone:{session_id}:{phone}:{chat_session.collected_data.get('date','')}:{chat_session.collected_data.get('time','')}"
        effect_state = _active_effect_state(chat_session)
        sent_keys = effect_state.get("otp_sent_keys")
        if not isinstance(sent_keys, list):
            sent_keys = []
            effect_state["otp_sent_keys"] = sent_keys
        otp_error = None
        if phone_event_key not in sent_keys:
            try:
                otp_code = await otp_crud.generate_otp(
                    db, chat_session.session_id, channel="phone", resend=False
                )
                # Delivery via provider abstraction (WhatsApp active, SMS fallback)
                try:
                    from app.core.config import settings as _cfg_otp
                    from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider
                    from app.core.otp_delivery.sms_provider import SMSProvider
                    _otp_provider = WhatsAppProvider() if (getattr(_cfg_otp, "OTP_PHONE_PROVIDER", "whatsapp") or "whatsapp").lower() == "whatsapp" else SMSProvider()
                    await _otp_provider.send_otp(phone, otp_code, str(chat_session.session_id))
                    logger.info(f"Phone OTP ({_otp_provider.__class__.__name__}) sent for {session_id}")
                except Exception as sms_err:
                    logger.error(f"Phone OTP send failed for {session_id}: {sms_err}")
                    raise
                sent_keys.append(phone_event_key)
                await crud_chat.update_chat_session(db, session_id, chat_session)
            except Exception as otp_send_error:
                otp_error = str(otp_send_error)
        try:
            otp_policy_meta = await otp_crud.get_otp_policy_metadata(
                db, chat_session.session_id, channel="phone"
            )
        except Exception:
            otp_policy_meta = {}
        directive = "REQUIRE_OTP_MODAL"
        field_meta.update(otp_policy_meta)
        field_meta["otp_required"] = True
        field_meta["channel"] = "phone"
        field_meta["otp_event_key"] = phone_event_key
        if otp_error:
            field_meta["validation_error"] = otp_error
        masked = phone[:4] + "***" + phone[-2:] if len(phone) > 6 else phone[:3] + "***"
        from app.core.config import settings as _cfg_via
        via = "WhatsApp" if (getattr(_cfg_via, "OTP_PHONE_PROVIDER", "whatsapp") or "whatsapp").lower() == "whatsapp" else "SMS"
        assistant_message = (
            f"Perfect! I've sent a verification code to {masked} via {via}. "
            "Please enter the 6-digit code here to confirm your booking."
        )
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
            # Enrich with meeting link from DB so SMTP mail shows Join Meeting (offline vs online)
            # LangGraph path previously used only collected_data, missing meeting_ fields -> mail showed Offline
            try:
                from app.models.booking import Booking
                from sqlalchemy import select as _sel
                _res = await db.execute(_sel(Booking).where(Booking.session_id == session_id, Booking.org_id == org.id).order_by(Booking.created_at.desc()).limit(1))
                _bk = _res.scalars().first()
                if _bk:
                    if getattr(_bk, "meeting_type_snapshot", None):
                        booking_details["meeting_type_snapshot"] = _bk.meeting_type_snapshot
                    if getattr(_bk, "meeting_link_status", None):
                        _st = _bk.meeting_link_status
                        booking_details["meeting_link_status"] = _st.value if hasattr(_st, "value") else str(_st)
                    if getattr(_bk, "meeting_link_url", None):
                        booking_details["meeting_link_url"] = _bk.meeting_link_url
                    if getattr(_bk, "meeting_link_provider", None):
                        _pv = _bk.meeting_link_provider
                        booking_details["meeting_link_provider"] = _pv.value if hasattr(_pv, "value") else str(_pv)
                    # Add dual time if missing
                    if getattr(_bk, "user_timezone", None) and "user_timezone" not in booking_details:
                        booking_details["user_timezone"] = _bk.user_timezone
                    if getattr(_bk, "org_timezone_snapshot", None) and "org_timezone_snapshot" not in booking_details:
                        booking_details["org_timezone_snapshot"] = _bk.org_timezone_snapshot
            except Exception as _enrich_err:
                logger.warning(f"Failed to enrich booking_details with meeting link for email: {_enrich_err}")
            customer_email = booking_details.get("email")
            admin_email = booking_details.get("admin_email")
            # Explicit: always send confirmation email if email present, even when email OTP was disabled and phone OTP verified
            if customer_email:
                logger.info(
                    "Booking confirmation email queued for %s via %s (session %s)",
                    customer_email,
                    "phone-otp" if getattr(chat_session, "phone_otp_verified", False) else "email-otp/none",
                    session_id,
                )
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
                # Convert UTC slot times to the effective timezone for display
                try:
                    from zoneinfo import ZoneInfo
                except ImportError:
                    from backports.zoneinfo import ZoneInfo  # type: ignore
                try:
                    _tz_for_slots = ZoneInfo(str(timezone_resolution.get("effective_timezone") or org_timezone or "UTC"))
                except Exception:
                    import datetime as _dt

                    _tz_for_slots = _dt.timezone.utc
                options = []
                for item in inventory:
                    if not item.get("available"):
                        continue
                    _start = item.get("start_utc")
                    if _start is None:
                        continue
                    try:
                        if getattr(_start, "tzinfo", None) is None:
                            import datetime as _dt2

                            _start = _start.replace(tzinfo=_dt2.timezone.utc)
                        _local_time = _start.astimezone(_tz_for_slots).time()
                        options.append(format_slot_time(_local_time))
                    except Exception:
                        try:
                            options.append(format_slot_time(item["start_utc"].time()))
                        except Exception:
                            continue
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

    # Proactive hours for WhatsApp parity: when asking for date/time, show
    # a concise hours line contextually as per message flow (no full paragraph)
    if directive in ("REQUIRE_DATE_PICKER", "REQUIRE_TIME_SLOT_GRID"):
        try:
            _short = "Hours: Mon-Fri 9:00 AM–5:00 PM, Sat 9:00 AM–4:00 PM, Sun Closed (Asia/Calcutta)"
            try:
                from app.engine.scheduling_policy import load_weekly_hours_block

                _full = await load_weekly_hours_block(db, str(org.id))
                if _full and "Monday" in _full:
                    _first = _full.split("\n")[0].replace("## ", "").replace(" (authoritative — answer timing questions from this)", "")
                    if len(_first) < 120:
                        _short = _first.strip()
            except Exception:
                pass
            if isinstance(assistant_message, str) and "Hours:" not in assistant_message and "business hours" not in assistant_message.lower():
                assistant_message = f"{_short}\n{assistant_message}"
        except Exception:
            pass

    # Enforce one-by-one flow: override LLM dump of "provide name, email, phone, date, time" with deterministic single-field ask
    _single_field_directives = {
        "TEXT_INPUT",
        "REQUIRE_EMAIL_INPUT",
        "REQUIRE_PHONE_INPUT",
        "REQUIRE_DATE_PICKER",
        "REQUIRE_TIME_SLOT_GRID",
        "REQUIRE_SELECT",
        "REQUIRE_TEXTAREA",
        "REQUIRE_NUMBER_INPUT",
    }
    if directive in _single_field_directives:
        # If LLM mentions 2+ fields or lists them, replace with deterministic single-field message
        _lower = (assistant_message or "").lower()
        _mentions = sum(1 for _kw in ["name", "email", "phone", "date", "time"] if _kw in _lower)
        if _mentions > 1 or ("," in assistant_message and _mentions >= 2):
            assistant_message = message_processor._build_context_aware_response(
                directive=directive,
                field_meta=field_meta,
                collected_data=dict(chat_session.collected_data or {}),
                valid_data={},
                errors={},
                llm_generated_msg=assistant_message,
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
