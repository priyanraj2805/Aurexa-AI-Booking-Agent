"""Meta WhatsApp Cloud API webhook — receive-only foundation (backend-only phase).

Scope (deliberately narrow):
  GET  /whatsapp/webhook  -> Meta verification handshake (hub.mode/challenge).
  POST /whatsapp/webhook  -> validate + parse + normalize Meta events, return 200.

This endpoint does NOT call LangGraph, runtime_router, BookingDirective
service/handler, booking creation, OTP, or any outbound sender. The Baileys
QR pipeline (`whatsapp_qr.qr_inbound`) is untouched and remains the live
booking transport. Wiring normalized `meta_cloud` events into the
session/directive pipeline happens in a later phase.

Inbound identity note: Meta `message.from` is the Cloud-API user identity
(wa_id digits). It is preserved verbatim and is NOT converted into a
Baileys LID — session identity mapping is a later phase.
"""

import logging
import re
import time as _time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.whatsapp_debouncer import get_session_lock, is_duplicate
from app.db.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Dedicated channel so Cloud (wa_id) identities never collide with
# Baileys (LID) identities. No conversion between the two, by design.
CLOUD_CHANNEL = "whatsapp_cloud"

# WABA this webhook belongs to. Compared against
# metadata.phone_number_id on every change value; never hardcoded in logic
# (see settings.META_WHATSAPP_PHONE_NUMBER_ID).
_PROVIDER = "meta_cloud"


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _expected_phone_number_id() -> str:
    return str(getattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "") or "").strip()


def _verify_token_configured() -> str:
    return str(getattr(settings, "META_WHATSAPP_WEBHOOK_VERIFY_TOKEN", "") or "")


def _normalize_message(message: dict, phone_number_id: str) -> Optional[dict]:
    """Map one Meta message object to the Aurexa normalized event.

    Keeps the internal `option_id` concept used by the Baileys path so the
    later engine phase can share the directive-tap code path. Returns None
    for objects without a usable provider message id.
    """
    if not isinstance(message, dict):
        return None
    message_id = str(message.get("id") or "").strip()
    if not message_id:
        return None
    msg_from = _digits(message.get("from"))
    raw_type = str(message.get("type") or "")
    timestamp = message.get("timestamp")

    text: Optional[str] = None
    option_id: Optional[str] = None
    message_type = "unknown"

    if raw_type == "text":
        body = message.get("text") or {}
        if isinstance(body, dict):
            text = str(body.get("body") or "")
        message_type = "text"
    elif raw_type == "interactive":
        interactive = message.get("interactive") or {}
        if isinstance(interactive, dict):
            sub = str(interactive.get("type") or "")
            if sub == "button_reply":
                reply = interactive.get("button_reply") or {}
                if isinstance(reply, dict):
                    option_id = str(reply.get("id") or "").strip() or None
                    text = str(reply.get("title") or "")
                message_type = "interactive"
                raw_type = "button_reply"
            elif sub == "list_reply":
                reply = interactive.get("list_reply") or {}
                if isinstance(reply, dict):
                    option_id = str(reply.get("id") or "").strip() or None
                    text = str(reply.get("title") or "")
                message_type = "interactive"
                raw_type = "list_reply"
            # Other interactive sub-types (e.g. nfm_reply from Flows) stay
            # message_type="unknown" with raw_type preserved for later phases.

    return {
        "provider": _PROVIDER,
        "phone_number_id": phone_number_id,
        "from": msg_from,
        "message_id": message_id,
        "message_type": message_type,
        "text": text,
        "option_id": option_id,
        "timestamp": timestamp,
        "raw_type": raw_type,
    }


@router.get("/webhook")
async def verify(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    """Meta verification handshake: echo hub.challenge iff mode + token match."""
    want = _verify_token_configured()
    # Never log the tokens themselves — match result only.
    if not want:
        logger.warning("meta webhook verify attempted with no verify token configured")
        return PlainTextResponse("Forbidden", status_code=403)
    if hub_mode == "subscribe" and hub_verify_token == want and hub_challenge:
        logger.info("meta webhook verified (handshake ok)")
        return PlainTextResponse(str(hub_challenge), status_code=200)
    logger.warning("meta webhook verify failed (mode/challenge mismatch)")
    return PlainTextResponse("Forbidden", status_code=403)


async def _resolve_booking_org(db: AsyncSession):
    """Org owning the Cloud booking flow: the org with the booking toggle ON.

    Single-tenant for now (one WhatsAppConnection row with booking enabled).
    Multi-org PNID->org mapping is a later phase; the PNID check above already
    guarantees the event belongs to our WABA number.
    """
    from app.models.organization import Organization
    from app.models.whatsapp_connection import WhatsAppConnection

    rows = (
        await db.execute(
            select(WhatsAppConnection)
            .where(WhatsAppConnection.whatsapp_booking_enabled.is_(True))
            .order_by(WhatsAppConnection.updated_at.desc())
        )
    ).scalars().all()
    for row in rows:
        org = (
            await db.execute(select(Organization).where(Organization.id == row.org_id))
        ).scalars().first()
        if org is not None and bool(getattr(org, "is_active", False)):
            return org, row
    return None, None


async def _process_booking_event(db: AsyncSession, background_tasks: BackgroundTasks, event: dict) -> bool:
    """Run one normalized Cloud event through the live booking pipeline.

    Mirrors `whatsapp_qr.qr_inbound` turn-for-turn (tap block -> engine ->
    directive render -> template hook) but sends the reply via Cloud API as
    text. Returns True if a reply path was executed. Never raises.
    """
    from_phone = str(event.get("from") or "")
    text = str(event.get("text") or "").strip()
    if len(from_phone) < 7 or not text:
        return False

    try:
        org, _conn = await _resolve_booking_org(db)
        if org is None:
            logger.info("meta webhook booking skipped (no org with booking enabled)")
            return False

        # Rate limit mirrors QR: OTP codes and knowledge questions exempt.
        is_otp_code = bool(re.match(r"^\d{6}$", text))
        is_knowledge = False
        try:
            from app.engine.agent.graph import _is_potential_knowledge_question

            is_knowledge = _is_potential_knowledge_question(text)
        except Exception:
            is_knowledge = False
        # Flood accounting is enforced AFTER session load so a burst gets a
        # spoken slowdown notice, never silence.
        _rate_exempt = bool(is_otp_code or is_knowledge)

        from app.crud import chat as crud_chat
        from app.engine import runtime_router
        from app.core import whatsapp_client, whatsapp_cloud, whatsapp_templates
        from app.models.config import OrgConfig

        cfg_res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
        org_config = cfg_res.scalars().first()
        if org_config is None:
            return False

        chat_session = await crud_chat.get_or_create_channel_session(
            db, str(org.id), CLOUD_CHANNEL, from_phone
        )
        # Human handover: admin owns the conversation — persist for the
        # inbox and send nothing. Returning True marks the event handled.
        try:
            if await crud_chat.is_human_takeover_active(db, str(chat_session.session_id)):
                try:
                    await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                except Exception:
                    pass
                return True
        except Exception:
            pass
        # Flood protection (sliding window — steady pacing never trips it).
        # A burst gets a spoken slowdown notice, never silence.
        if not _rate_exempt:
            from app.core.whatsapp_debouncer import check_sender_rate_limit as _check_rl

            _limited, _retry_after = _check_rl(f"meta:{from_phone}")
            if _limited:
                _wait = max(1, int(round(_retry_after)))
                _slow_body = (
                    f"You're sending messages very quickly — please wait about "
                    f"{_wait} seconds and send that again."
                )
                try:
                    await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                    await crud_chat.save_message(db, str(chat_session.session_id), "agent", _slow_body)
                except Exception:
                    pass
                try:
                    await whatsapp_cloud.send_booking_reply(
                        from_phone, {"type": "text", "body": _slow_body[:4096]}
                    )
                except Exception:
                    pass
                logger.warning("meta webhook burst slowed (retry in ~%ss)", _wait)
                return True
        # Structured directive tap — same pre-LLM handling as the QR path.
        try:
            _active_id = None
            if isinstance(chat_session.collected_data, dict):
                _active_id = chat_session.collected_data.get("active_directive_id")
            if _active_id:
                from app.engine.booking_directive.service import get_by_id as _get_dir
                from app.engine.booking_directive import handler as _dir_handler

                _dir = await _get_dir(db, _active_id)
                # Availability/hours inquiries bypass directive interception
                # entirely: the engine answers from the live slot inventory
                # (or weekly hours), then re-asks the pending step. Without
                # this, a question is validated as step input and the user is
                # interrogated instead of answered. Explicit taps (option_id)
                # always take precedence.
                _inquiry_bypass = False
                if not str(event.get("option_id") or "").strip():
                    try:
                        from app.engine.scheduling_policy import (
                            is_available_dates_query as _is_dates_q,
                            is_schedule_business_hours_query as _is_hours_q,
                        )
                        if _is_dates_q(text) or _is_hours_q(text):
                            _inquiry_bypass = True
                            logger.info("Directive interception bypassed for inquiry (cloud)")
                    except Exception:
                        pass
                if _dir and _dir.status == "PENDING" and _dir.customer_id == from_phone and not _inquiry_bypass:
                    from datetime import datetime, timezone as _tz
                    # Shared strict step guards (same module as the QR path).
                    from app.engine import whatsapp_guards as _guards

                    if _dir.expires_at and _dir.expires_at < datetime.now(_tz.utc):
                        _dir.status = "EXPIRED"
                        await db.flush()
                        logger.info(f"Directive {str(_dir.id)[:8]} expired on tap (cloud)")
                    else:
                        _matched = None
                        # Bound for every path: synthetic typed-date ISO or None.
                        _typed_iso = None
                        # True when handed to the engine untouched (cancel /
                        # cross-field change) — step guards below skip.
                        _step_handled = False
                        _incoming_oid = str(event.get("option_id") or "").strip()
                        if _incoming_oid:
                            for _opt in (_dir.options or []):
                                if str(_opt.get("id") or "").strip() == _incoming_oid:
                                    _matched = _opt
                                    break
                        # Objection handling FIRST (same contract as QR path):
                        # acknowledge in the admin-chosen tone, never slap a
                        # format correction on a genuine concern.
                        if not _matched:
                            _dtype_o = str(getattr(_dir, "type", "") or "")
                            _step_field_o = {"SELECT_DATE": "date", "SELECT_SLOT": "time", "SELECT_TIME": "time"}.get(_dtype_o)
                            _cd_o = dict(chat_session.collected_data or {})
                            _objection = _guards.detect_objection(text, _cd_o, _step_field_o)
                            if _objection:
                                _brief = _guards.build_objection_brief(
                                    category=str(_objection.get("category") or ""),
                                    field=_objection.get("field"),
                                    business_name=str(
                                        ((getattr(org_config, "custom_prompts", None) or {}).get("business_name"))
                                        or getattr(org, "name", "")
                                        or ""
                                    ),
                                    tone=(getattr(org_config, "custom_prompts", None) or {}).get("tone"),
                                    field_label=_guards.field_slot(
                                        getattr(org_config, "field_definitions", None),
                                        _objection.get("field"),
                                    )[0],
                                    field_description=_guards.field_slot(
                                        getattr(org_config, "field_definitions", None),
                                        _objection.get("field"),
                                    )[1],
                                    field_required=_guards.field_slot(
                                        getattr(org_config, "field_definitions", None),
                                        _objection.get("field"),
                                    )[2],
                                    required_labels=_guards.required_labels(
                                        getattr(org_config, "field_definitions", None)
                                    ),
                                    step_title=str(getattr(_dir, "title", "") or ""),
                                    step_prompt=str(getattr(_dir, "description", "") or ""),
                                )
                                try:
                                    from app.engine.whatsapp_presenter import WhatsAppPresenter as _WPPresenter

                                    _obj_resp = await _WPPresenter().present_objection(_brief)
                                    _obj_body = str(_obj_resp.body or "").strip() or "Noted — how else can I help with your booking?"
                                except Exception as _obj_e:
                                    logger.warning(f"Objection presentation failed (cloud), engine fallback: {_obj_e}")
                                    _obj_body = ""
                                if _obj_body:
                                    try:
                                        await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                                        await crud_chat.save_message(db, str(chat_session.session_id), "agent", _obj_body)
                                    except Exception:
                                        pass
                                    try:
                                        await whatsapp_cloud.send_booking_reply(
                                            from_phone, {"type": "text", "body": _obj_body[:4096]}
                                        )
                                    except Exception:
                                        pass
                                    logger.info(f"Directive {str(_dir.id)[:8]} objection acknowledged (cloud) ({_brief.get('category')}): {text[:40]!r}")
                                    return True
                        if _incoming_oid:
                            for _opt in (_dir.options or []):
                                if str(_opt.get("id") or "").strip() == _incoming_oid:
                                    _matched = _opt
                                    break
                        if not _matched:
                            # Strict step guards: exact option text always;
                            # positional index ONLY for time slots. Bare numbers
                            # are NEVER a date (prompts show no numbers).
                            _dtype_m = str(getattr(_dir, "type", "") or "")
                            _opts_m = list(_dir.options or [])
                            _matched = _guards.match_option_by_text(_opts_m, text)
                            if (
                                not _matched
                                and _dtype_m in ("SELECT_SLOT", "SELECT_TIME")
                            ):
                                _matched = _guards.match_slot_index(_opts_m, text)
                            # Flexible time match: "3 pm" / "3pm" / "3:00 pm".
                            if not _matched and str(getattr(_dir, "type", "")) in ("SELECT_SLOT", "SELECT_TIME"):
                                try:
                                    from app.engine.time_flex import match_time_to_slot_label as _match_slot

                                    _labels = [str(o.get("label") or "") for o in (_dir.options or [])]
                                    _hit = _match_slot(text, _labels)
                                    if _hit:
                                        for _opt in (_dir.options or []):
                                            if str(_opt.get("label") or "") == _hit:
                                                _matched = _opt
                                                break
                                except Exception:
                                    pass
                            # Flexible confirm match (typo-tolerant).
                            if not _matched and str(getattr(_dir, "type", "")) == "CONFIRM_BOOKING":
                                try:
                                    from app.engine.time_flex import is_confirm_message as _is_confirm

                                    if _is_confirm(text):
                                        for _opt in (_dir.options or []):
                                            _oid_l = str(_opt.get("id") or "").lower()
                                            if "confirm" in _oid_l or str(_opt.get("value") or "").lower() == "confirm":
                                                _matched = _opt
                                                break
                                except Exception:
                                    pass
                            # Strict typed-date acceptance (SELECT_DATE only): a valid
                            # in-horizon date completes the step even when not
                            # listed. Anything else → exact-format correction.
                            _typed_iso = None
                            if not _matched and str(getattr(_dir, "type", "")) == "SELECT_DATE":
                                _tz_g = str(getattr(org_config, "timezone", None) or "UTC")
                                try:
                                    from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                                    _max_g = (await _resolve_horizon(db, org.id, org_config))[0]
                                except Exception:
                                    _max_g = 30
                                _iso_g, _corr_g, _reason_g = _guards.validate_typed_date(
                                    text,
                                    tz=_tz_g,
                                    max_days=_max_g,
                                    examples=_guards.example_date_hints(
                                        [str(o.get("value") or "") for o in (_dir.options or [])],
                                        tz=_tz_g,
                                        max_days=_max_g,
                                    ),
                                )
                                if _iso_g:
                                    _typed_iso = _iso_g
                                    _matched = {
                                        "id": f"date_typed_{_iso_g.replace('-', '')}",
                                        "label": _iso_g,
                                        "value": _iso_g,
                                    }
                                else:
                                    # Not a date value: let the LLM decide (lookup /
                                    # cancel / change / questions reach the engine).
                                    _bypass_c = False
                                    try:
                                        from app.engine import check_booking_flow as _check_flow_c

                                        _bypass_c = await _check_flow_c.should_bypass_step_guard(text)
                                    except Exception:
                                        _bypass_c = False
                                    if _bypass_c:
                                        _dir.status = "EXPIRED"
                                        _cd_bc = dict(chat_session.collected_data or {})
                                        _cd_bc.pop("active_directive_id", None)
                                        chat_session.collected_data = _cd_bc
                                        try:
                                            await db.commit()
                                        except Exception:
                                            pass
                                        logger.info(f"Directive {str(_dir.id)[:8]} LLM bypass to engine (cloud): {text[:40]!r}")
                                        _step_handled = True
                                    else:
                                        _horizon_g = str(getattr(_dir, "description", "") or "").strip()
                                        _wrong_body = _guards.date_correction(text, _corr_g or "", _horizon_g)
                                        try:
                                            await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                                            await crud_chat.save_message(db, str(chat_session.session_id), "agent", _wrong_body)
                                        except Exception:
                                            pass
                                        try:
                                            await whatsapp_cloud.send_booking_reply(
                                                from_phone, {"type": "text", "body": _wrong_body[:4096]}
                                            )
                                        except Exception:
                                            pass
                                        logger.info(f"Directive {str(_dir.id)[:8]} wrong-date corrected ({_reason_g}) (cloud): {text[:40]!r}")
                                        return True
                            # Change-intent on CONFIRM_BOOKING.
                            if not _matched and str(getattr(_dir, "type", "")) == "CONFIRM_BOOKING":
                                try:
                                    if _guards.match_confirm_intent(text) == "change":
                                        for _opt in (_dir.options or []):
                                            _oid_l = str(_opt.get("id") or "").lower()
                                            if "change" in _oid_l or str(_opt.get("value") or "").lower() == "change":
                                                _matched = _opt
                                                break
                                except Exception:
                                    pass
                            # Cancel bypass + cross-field change routing (same as
                            # QR path): expire the stale step, apply pops, fall
                            # through with ORIGINAL text to the engine.
                            if not _matched:
                                _dtype_h = str(getattr(_dir, "type", "") or "")
                                _step_field = {"SELECT_DATE": "date", "SELECT_SLOT": "time", "SELECT_TIME": "time"}.get(_dtype_h)
                                _cd_h = dict(chat_session.collected_data or {})
                                if _guards.is_cancel_bypass(text):
                                    _dir.status = "EXPIRED"
                                    _cd_h.pop("active_directive_id", None)
                                    chat_session.collected_data = _cd_h
                                    try:
                                        await db.commit()
                                    except Exception:
                                        pass
                                    logger.info(f"Directive {str(_dir.id)[:8]} cancel bypass to engine (cloud): {text[:40]!r}")
                                    _step_handled = True
                                else:
                                    _change_field = _guards.detect_field_change_request(
                                        text, _cd_h, _step_field
                                    )
                                    if _change_field:
                                        _dir.status = "EXPIRED"
                                        _cd_h = _guards.apply_collected_field_change(_cd_h, _change_field)
                                        _cd_h.pop("active_directive_id", None)
                                        chat_session.collected_data = _cd_h
                                        try:
                                            await db.commit()
                                        except Exception:
                                            pass
                                        logger.info(f"Directive {str(_dir.id)[:8]} field-change ({_change_field}) to engine (cloud): {text[:40]!r}")
                                        _step_handled = True
                            # Wrong-slot guard: LLM bypass first so lookup/cancel /
                            # change / questions reach the engine, never a correction.
                            if not _matched and not _step_handled and str(getattr(_dir, "type", "")) in ("SELECT_SLOT", "SELECT_TIME", "SELECT_DATE"):
                                _llm_bypass_c2 = False
                                try:
                                    from app.engine import check_booking_flow as _check_flow_c2

                                    _llm_bypass_c2 = await _check_flow_c2.should_bypass_step_guard(text)
                                except Exception:
                                    _llm_bypass_c2 = False
                                if _llm_bypass_c2:
                                    _dir.status = "EXPIRED"
                                    _cd_bc2 = dict(chat_session.collected_data or {})
                                    _cd_bc2.pop("active_directive_id", None)
                                    chat_session.collected_data = _cd_bc2
                                    try:
                                        await db.commit()
                                    except Exception:
                                        pass
                                    logger.info(f"Directive {str(_dir.id)[:8]} LLM bypass to engine (cloud slot guard): {text[:40]!r}")
                                    _step_handled = True
                            if not _matched and not _step_handled and str(getattr(_dir, "type", "")) in ("SELECT_SLOT", "SELECT_TIME", "SELECT_DATE"):
                                _dtype_c = str(getattr(_dir, "type", "") or "")
                                if _dtype_c == "SELECT_DATE":
                                    _horizon2 = str(getattr(_dir, "description", "") or "").strip()
                                    _tz2 = str(getattr(org_config, "timezone", None) or "UTC")
                                    try:
                                        from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                                        _max2 = (await _resolve_horizon(db, org.id, org_config))[0]
                                    except Exception:
                                        _max2 = 30
                                    _ex2 = _guards.example_date_hints(
                                        [str(o.get("value") or "") for o in (_dir.options or [])],
                                        tz=_tz2,
                                        max_days=_max2,
                                    )
                                    _hint2 = " or ".join(f"*{h}*" for h in _ex2) if _ex2 else "the day, month and year in full"
                                    _wrong_body = _guards.date_correction(
                                        text,
                                        f"Please type the meeting date like {_hint2}.",
                                        _horizon2,
                                    )
                                else:
                                    _avail_c = [str(o.get("label") or "").strip() for o in (_dir.options or []) if str(o.get("label") or "").strip()]
                                    _hours = ""
                                    if _avail_c:
                                        _hours = "Available times: " + " · ".join(_avail_c[:7]) + "."
                                    _wrong_body = _guards.time_correction(
                                        text, _hours, examples=_guards.example_time_hints(_avail_c)
                                    )
                                try:
                                    await crud_chat.save_message(db, str(chat_session.session_id), "user", text)
                                    await crud_chat.save_message(db, str(chat_session.session_id), "agent", _wrong_body)
                                except Exception:
                                    pass
                                try:
                                    await whatsapp_cloud.send_booking_reply(
                                        from_phone, {"type": "text", "body": _wrong_body[:4096]}
                                    )
                                except Exception:
                                    pass
                                logger.info(f"Directive {str(_dir.id)[:8]} wrong-slot corrected (cloud): {text[:40]!r}")
                                return True
                        if _matched:
                            if _typed_iso:
                                # Synthetic typed date: complete directly.
                                try:
                                    from app.engine.booking_directive.service import complete as _complete_dir

                                    _syn = {
                                        "id": f"date_typed_{_typed_iso.replace('-', '')}",
                                        "label": _typed_iso,
                                        "value": _typed_iso,
                                    }
                                    await _complete_dir(db, _dir.id, _syn["id"], booking_id=None)
                                    try:
                                        _row2 = await _get_dir(db, _dir.id)
                                        if _row2 is not None:
                                            _row2.selected_option = _syn
                                            await db.flush()
                                    except Exception:
                                        pass
                                    _cd2 = dict(chat_session.collected_data or {})
                                    _cd2["date"] = _typed_iso
                                    _cd2.pop("active_directive_id", None)
                                    chat_session.collected_data = _cd2
                                    from app.engine.evaluator import StateEvaluator as _SE2
                                    from app.schemas.agent import parse_field_definitions as _pfd2, order_field_definitions as _ofd2

                                    _fds2 = _ofd2(_pfd2(chat_session.collected_data.get("field_definitions_snapshot") or org_config.field_definitions or org_config.required_fields))
                                    _req2 = [f.name for f in _fds2 if f.required]
                                    chat_session.missing_fields = _SE2.calculate_missing_fields(_req2, _cd2)
                                    await db.commit()
                                    logger.info(f"Directive {str(_dir.id)[:8]} completed via typed date (cloud): {_typed_iso}")
                                except Exception as _e2:
                                    logger.warning(f"Directive typed-date completion failed (cloud): {_e2}")
                                text = _typed_iso
                                _res = {"ok": True}
                            else:
                                _res = await _dir_handler.handle_directive_response(
                                    db, directive_id=_dir.id, customer_id=from_phone,
                                    option_id=_matched.get("id"), booking_id=None,
                                )
                            if _res.get("ok"):
                                try:
                                    _field = (_dir.meta or {}).get("field_name") or (_dir.type.lower().replace("select_", "") if _dir.type.startswith("SELECT_") else None)
                                    if _field:
                                        _cd = dict(chat_session.collected_data or {})
                                        _field_map = {"SELECT_DATE": "date", "SELECT_SLOT": "time", "SELECT_TIME": "time"}
                                        _fname = _field_map.get(_dir.type, _field)
                                        _cd[_fname] = _matched.get("value") or _matched.get("label")
                                        _cd.pop("active_directive_id", None)
                                        chat_session.collected_data = _cd
                                        from app.engine.evaluator import StateEvaluator
                                        from app.schemas.agent import parse_field_definitions, order_field_definitions

                                        _fds = order_field_definitions(parse_field_definitions(chat_session.collected_data.get("field_definitions_snapshot") or org_config.field_definitions or org_config.required_fields))
                                        _req = [f.name for f in _fds if f.required]
                                        chat_session.missing_fields = StateEvaluator.calculate_missing_fields(_req, _cd)
                                        await db.commit()
                                        logger.info(f"Directive {str(_dir.id)[:8]} completed via tap (cloud): {str(_matched.get('label'))[:20]}")
                                except Exception as _e:
                                    logger.warning(f"Directive tap state update failed (cloud): {_e}")
                                # CHANGE keeps ORIGINAL text so a new value inside
                                # ("change my phone to 98...") reaches extraction.
                                _is_change_opt = "change" in str((_matched or {}).get("id") or "").lower() or str((_matched or {}).get("value") or "").lower() == "change"
                                if not _is_change_opt:
                                    text = str(_matched.get("value") or _matched.get("label") or text)
        except Exception as _e:
            logger.warning(f"Directive tap handling failed (cloud), falling back to LLM: {_e}")

        session_lock = get_session_lock(f"cloud:{str(chat_session.session_id)}")
        async with session_lock:
            _t0 = _time.monotonic()
            action = await runtime_router.route_whatsapp_message(
                db=db,
                org=org,
                org_config=org_config,
                chat_session=chat_session,
                message_text=text,
                background_tasks=background_tasks,
            )
            _dt = _time.monotonic() - _t0
            _active_id_after = None
            try:
                if isinstance(chat_session.collected_data, dict):
                    _active_id_after = chat_session.collected_data.get("active_directive_id")
            except Exception:
                pass
            # ── Simple text booking system — directive interactive disabled ──
            wa_msg = None
            if _active_id_after:
                try:
                    from app.engine.booking_directive.service import get_by_id as _get_dir2

                    _dir_after = await _get_dir2(db, _active_id_after)
                    if _dir_after and _dir_after.status == "PENDING":
                        _title = str(getattr(_dir_after, "title", "") or "").strip()
                        _desc = str(getattr(_dir_after, "description", "") or "").strip()
                        _opts = list(getattr(_dir_after, "options", None) or [])
                        if not _desc:
                            try:
                                if _dir_after.type == "SELECT_DATE":
                                    from app.engine.message_processor import _format_available_dates_for_prompt
                                    _tz_c2 = str(getattr(org_config, "timezone", None) or "UTC")
                                    from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon
                                    _max_c2 = (await _resolve_horizon(db, org.id, org_config))[0]
                                    _desc = _format_available_dates_for_prompt(_tz_c2, _max_c2)
                                elif _dir_after.type in ("SELECT_SLOT", "SELECT_TIME"):
                                    from app.engine.message_processor import _format_time_prompt_for_whatsapp
                                    from app.models.booking import BusinessSchedule as _BSc2, ScheduleDay as _SDc2, ScheduleException as _SEc2
                                    from sqlalchemy.future import select as _Selc2
                                    _sel_date_c2 = (chat_session.collected_data or {}).get("date") or ""
                                    _weekly_c2 = None
                                    _excs_c2 = None
                                    try:
                                        _sdc2_res = await db.execute(_Selc2(_SDc2).where(_SDc2.org_id == org.id))
                                        _sdc2 = _sdc2_res.scalars().all()
                                        _weekly_c2 = [{"day_of_week": r.day_of_week, "is_active": r.is_active, "open_time": r.open_time, "close_time": r.close_time} for r in _sdc2]
                                        _sec2_res = await db.execute(_Selc2(_SEc2).where(_SEc2.org_id == org.id))
                                        _sec2 = _sec2_res.scalars().all()
                                        _excs_c2 = [{"exception_date": r.exception_date, "exception_type": str(r.exception_type), "custom_open": r.custom_open, "custom_close": r.custom_close} for r in _sec2]
                                    except Exception:
                                        pass
                                    _slot_mins_c2 = None
                                    try:
                                        _bsc2_res = await db.execute(_Selc2(_BSc2).where(_BSc2.org_id == org.id))
                                        _bsc2 = _bsc2_res.scalars().first()
                                        if _bsc2:
                                            _slot_mins_c2 = getattr(_bsc2, "slot_duration_minutes", None)
                                    except Exception:
                                        pass
                                    _tz_c2b = str(getattr(org_config, "timezone", None) or "UTC")
                                    _desc = _format_time_prompt_for_whatsapp(str(_sel_date_c2 or ""), _tz_c2b, _slot_mins_c2, weekly_schedule=_weekly_c2, exceptions=_excs_c2)
                            except Exception:
                                pass
                        _parts = [p for p in [_title, _desc] if p]
                        if _dir_after.type in ("SELECT_SLOT", "SELECT_TIME"):
                            _opts_c = list(getattr(_dir_after, "options", None) or [])
                            if _opts_c:
                                _labels_c = [str(o.get('label') or '').strip() for o in _opts_c[:7] if o.get('label')]
                                if _labels_c:
                                    _parts.append("Available slots: " + " · ".join(_labels_c))
                                    _parts.append("You can type any format — e.g. 3pm, 3 pm or 3:00 PM.")
                        if _dir_after.type == "CONFIRM_BOOKING":
                            _summary = str(getattr(action, "message", "") or "").strip()
                            if _summary and _summary not in _parts:
                                _parts.append(_summary)
                            _parts.append("Reply CONFIRM to confirm or CHANGE to modify.")
                        _body = "\n\n".join(_parts).strip()
                        # Same knowledge-answer combine as the QR transport:
                        # never drop the engine's answer behind the step.
                        try:
                            _fm_c = getattr(action, "field_meta", None) or {}
                            if isinstance(_fm_c, dict) and _fm_c.get("knowledge_answer"):
                                _body = whatsapp_client.attach_knowledge_answer(
                                    getattr(action, "message", ""), _body
                                )
                        except Exception:
                            pass
                        wa_msg = {"type": "text", "body": _body[:4096]}
                        logger.info(f"Rendered simple-text directive (cloud) type={_dir_after.type} directive={str(_dir_after.id)[:8]}")
                except Exception as _e:
                    logger.warning(f"Directive render failed (cloud), falling back: {_e}")
            if wa_msg is None:
                wa_msg = whatsapp_client.format_action_for_whatsapp(action)
                # Final anti-hallucination gate (same as QR path): block
                # invented contact/money entities. Fail-open on error.
                try:
                    from app.engine import knowledge_guards as _kb_guards

                    _body0 = str((wa_msg or {}).get("body") or "")
                    if _body0 and str((wa_msg or {}).get("type") or "") == "text":
                        _cd0 = dict(chat_session.collected_data or {})
                        _facts0: list = [str(getattr(org, "name", "") or "")]
                        try:
                            from app.engine.knowledge_profile import format_business_profile as _fbp

                            _facts0.append(_fbp(getattr(org_config, "knowledge_context", None)) or "")
                        except Exception:
                            pass
                        try:
                            from sqlalchemy.future import select as _SelFee
                            from app.models.service_unit import ProviderFee as _FeeModel

                            _fees = (
                                await db.execute(
                                    _SelFee(_FeeModel).where(
                                        _FeeModel.org_id == org.id,
                                        _FeeModel.is_active.is_(True),
                                    )
                                )
                            ).scalars().all()
                            for _f in _fees:
                                if _f.amount_paise:
                                    _facts0.append(f"{(_f.currency or 'INR')} {(_f.amount_paise / 100):,.2f}")
                        except Exception:
                            pass
                        _sess0 = [str(_cd0.get(k) or "") for k in ("name", "email", "phone", "date", "time")]
                        _safe0, _blocked0, _ents0 = _kb_guards.guard_final_text(
                            _body0, org_facts=_facts0, session_details=_sess0
                        )
                        if _blocked0:
                            logger.warning("whatsapp KB entity gate blocked reply (cloud) with unlisted %s", _ents0)
                            try:
                                await crud_chat.save_message(db, str(chat_session.session_id), "agent", _safe0)
                            except Exception:
                                pass
                            wa_msg = {"type": "text", "body": _safe0[:4096]}
                except Exception as _gate_e:
                    logger.warning(f"whatsapp KB entity gate failed open (cloud): {_gate_e}")
            # Approved Marketing templates (language `en`) for the contact
            # steps; the text `wa_msg` below is still sent as the reply.
            # Guard: one send per (template, missing-field set) — a stuck
            # step must not re-fire a billable template every turn.
            try:
                _directive = getattr(action, "widget_directive", "") or ""
                _fmeta = getattr(action, "field_meta", None) or {}
                _sdata = chat_session.collected_data if isinstance(getattr(chat_session, "collected_data", None), dict) else {}
                _cname = str(_sdata.get("name") or "").strip() or "there"
                _business = str(getattr(org, "name", "") or "Dotstark Technologies")[:60]
                _missing = list(getattr(chat_session, "missing_fields", None) or [])
                _tname = None
                if _directive == "REQUIRE_EMAIL_INPUT":
                    _tname = whatsapp_templates.TEMPLATE_REQUEST_EMAIL
                elif _directive == "REQUIRE_PHONE_INPUT":
                    _tname = whatsapp_templates.TEMPLATE_REQUEST_PHONE
                elif _directive == "TEXT_INPUT" and str(_fmeta.get("name") or "") == "name":
                    _tname = whatsapp_templates.TEMPLATE_WELCOME
                if _tname and whatsapp_templates.step_template_already_sent(_sdata, _tname, _missing):
                    logger.info("booking-template %s (cloud) skipped: already sent for this step", _tname)
                elif _tname:
                    if _tname == whatsapp_templates.TEMPLATE_REQUEST_EMAIL:
                        _tres = await whatsapp_templates.send_request_email(from_phone, _cname)
                    elif _tname == whatsapp_templates.TEMPLATE_REQUEST_PHONE:
                        _tres = await whatsapp_templates.send_request_phone(from_phone)
                    else:
                        _tres = await whatsapp_templates.send_welcome(from_phone, _cname, _business)
                    logger.info("booking-template %s (cloud) sent=%s wamid=%s", _tname, _tres.get("success"), _tres.get("wamid"))
                    if _tres.get("success"):
                        chat_session.collected_data = whatsapp_templates.mark_step_template_sent(
                            _sdata, _tname, _missing)
                        await crud_chat.update_chat_session(db, str(chat_session.session_id), chat_session)
            except Exception as _e:
                logger.warning(f"booking-template cloud send failed: {_e}")
            # Human handover won the race after the early check: stay silent.
            if str(getattr(action, "widget_directive", "") or "") == "HUMAN_ACTIVE":
                return True
            sent = await whatsapp_cloud.send_booking_reply(from_phone, wa_msg)
            logger.info(
                "meta webhook booking turn done in %.1fs reply_type=%s cloud_sent=%s",
                _dt, (wa_msg or {}).get("type"), sent.get("success"),
            )
            return True
    except Exception:
        logger.exception("meta webhook booking turn failed")
        return False


@router.post("/webhook")
async def inbound(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Receive Meta events, normalize message events, always answer fast.

    - Non-JSON / non-object payloads -> 400 (no point retrying poison bodies).
    - Wrong `object` type, unknown fields, status-only changes -> 200 ignored.
    - Unexpected phone_number_id -> logged, skipped as booking event, 200.
    - Duplicates (Meta retries) -> marked via the shared debouncer
      (`meta:<wamid>` namespace, same mechanism as `qr:<messageId>`), 200.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "invalid_payload"}

    if payload.get("object") != "whatsapp_business_account":
        return {"status": "ignored", "reason": "unknown_object"}

    expected_pnid = _expected_phone_number_id()
    events: list[dict] = []
    duplicates = 0
    statuses = 0
    skipped_pnid = 0

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return {"status": "ok", "provider": _PROVIDER, "events": [], "duplicates": 0, "statuses": 0}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            if change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            pnid = str((value.get("metadata") or {}).get("phone_number_id") or "") if isinstance(value.get("metadata"), dict) else ""
            if expected_pnid and pnid and pnid != expected_pnid:
                logger.warning(
                    "meta webhook unexpected phone_number_id tail=%s (configured tail=%s)",
                    pnid[-4:],
                    expected_pnid[-4:],
                )
                skipped_pnid += 1
                continue

            for status in value.get("statuses") or []:
                if isinstance(status, dict):
                    statuses += 1

            messages = value.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                event = _normalize_message(message, pnid)
                if event is None:
                    continue
                # Shared dedup mechanism (whatsapp_debouncer), Meta namespace.
                # Marks on first sight; retries report as duplicates.
                if is_duplicate(f"meta:{event['message_id']}"):
                    duplicates += 1
                    event = {**event, "duplicate": True}
                events.append(event)

    # Boundary log: counts + tails only, never message content or secrets.
    logger.info(
        "meta webhook inbound events=%s duplicates=%s statuses=%s skipped_pnid=%s",
        len(events),
        duplicates,
        statuses,
        skipped_pnid,
    )
    # Booking pipeline (wired phase): only fresh text/interactive events with
    # a usable sender run the engine. Duplicates/unknown types/statuses stop
    # here. Failures never change the 200 — Meta must not retry-loops us.
    processed = 0
    for event in events:
        try:
            if event.get("duplicate"):
                continue
            if event.get("message_type") not in ("text", "interactive"):
                continue
            if await _process_booking_event(db, background_tasks, event):
                processed += 1
        except Exception:
            logger.exception("meta webhook event loop failed")
    return {
        "status": "ok",
        "provider": _PROVIDER,
        "events": events,
        "duplicates": duplicates,
        "statuses": statuses,
        "processed": processed,
    }
