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

from cachetools import TTLCache
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.whatsapp_debouncer import get_session_lock, is_duplicate
from app.db.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Cloud inbound per-sender rate limit — mirrors the QR transport
# (max 10 messages per sender per 60 seconds; OTP/knowledge exempt).
_inbound_rate_cache: TTLCache = TTLCache(maxsize=10000, ttl=60)

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
        if not is_otp_code and not is_knowledge:
            count = _inbound_rate_cache.get(from_phone, 0)
            if count >= 10:
                logger.warning("meta webhook rate limit exceeded")
                return False
            _inbound_rate_cache[from_phone] = count + 1

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
        # Structured directive tap — same pre-LLM handling as the QR path.
        try:
            _active_id = None
            if isinstance(chat_session.collected_data, dict):
                _active_id = chat_session.collected_data.get("active_directive_id")
            if _active_id:
                from app.engine.booking_directive.service import get_by_id as _get_dir
                from app.engine.booking_directive import handler as _dir_handler

                _dir = await _get_dir(db, _active_id)
                if _dir and _dir.status == "PENDING" and _dir.customer_id == from_phone:
                    from datetime import datetime, timezone as _tz

                    if _dir.expires_at and _dir.expires_at < datetime.now(_tz.utc):
                        _dir.status = "EXPIRED"
                        await db.flush()
                        logger.info(f"Directive {str(_dir.id)[:8]} expired on tap (cloud)")
                    else:
                        _matched = None
                        _incoming_oid = str(event.get("option_id") or "").strip()
                        if _incoming_oid:
                            for _opt in (_dir.options or []):
                                if str(_opt.get("id") or "").strip() == _incoming_oid:
                                    _matched = _opt
                                    break
                        if not _matched:
                            _norm_text = text.strip().lower()
                            for _opt in (_dir.options or []):
                                _label = str(_opt.get("label") or "").strip().lower()
                                _value = str(_opt.get("value") or "").strip().lower()
                                _oid = str(_opt.get("id") or "").strip().lower()
                                if _norm_text in (_label, _value, _oid) or _norm_text == _label.replace(" ", ""):
                                    _matched = _opt
                                    break
                                try:
                                    _idx = int(_norm_text) - 1
                                    if 0 <= _idx < len(_dir.options or []) and _dir.options[_idx] == _opt:
                                        _matched = _opt
                                        break
                                except Exception:
                                    pass
                        if _matched:
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
                                    _max_c2 = int(getattr(org_config, "max_booking_days_ahead", 30) or 30)
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
                        _body = "\n\n".join(_parts).strip()
                        wa_msg = {"type": "text", "body": _body[:4096]}
                        logger.info(f"Rendered simple-text directive (cloud) type={_dir_after.type} directive={str(_dir_after.id)[:8]} (no options list per simple-text mode)")
                except Exception as _e:
                    logger.warning(f"Directive render failed (cloud), falling back: {_e}")
            if wa_msg is None:
                wa_msg = whatsapp_client.format_action_for_whatsapp(action)
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
