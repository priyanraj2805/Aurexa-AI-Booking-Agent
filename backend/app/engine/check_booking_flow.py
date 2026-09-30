"""Generic booking-lookup flow — LLM-driven, no hardcoded phrases.

Triggered ONLY when the LLM classifies the user's intent as CHECK_BOOKING.
Never inspects message text with regex/keywords. Resolves contact identity
from already-collected session data, queries live bookings, and renders them
from DB values + org timezone. Booking progress (collected_data /
missing_fields) is preserved so the user can resume booking afterwards.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.models.booking import Booking, TimeSlot
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.schemas.agent import ActionPayload, ChatSessionState
from app.crud import chat as crud_chat

logger = logging.getLogger(__name__)

# Collected-data flag marking a lookup that is waiting for the user to
# supply their contact. While set, the next message carrying an email or
# phone number completes the lookup instead of feeding the booking flow.
CHECK_PENDING_KEY = "_check_booking_pending"

_UPCOMING_LIMIT = 5
_PAST_LIMIT = 5

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Windows fallback
    from backports.zoneinfo import ZoneInfo


def _org_timezone_name(org_config: OrgConfig) -> str:
    return str(getattr(org_config, "timezone", None) or "UTC")


def _resolve_contact(collected: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return (email, phone_digits) from collected session data, if present."""
    email = collected.get("email") if isinstance(collected, dict) else None
    phone = collected.get("phone") if isinstance(collected, dict) else None
    if isinstance(email, str):
        email = email.strip() or None
    else:
        email = None
    digits: Optional[str] = None
    if isinstance(phone, str) and phone.strip():
        digits = re.sub(r"\D", "", phone)
        if not (10 <= len(digits) <= 15):
            digits = None
    elif phone is not None:
        raw = re.sub(r"\D", "", str(phone))
        if 10 <= len(raw) <= 15:
            digits = raw
    return email, digits


async def _find_bookings(
    db: AsyncSession,
    org: Organization,
    email: Optional[str],
    phone_digits: Optional[str],
    limit: int = _UPCOMING_LIMIT,
) -> List[Tuple[Booking, Optional[datetime]]]:
    """Find upcoming bookings for this org by email and/or phone."""
    return await _find_bookings_where(
        db, org, email, phone_digits, upcoming=True, limit=limit
    )


async def _find_past_bookings(
    db: AsyncSession,
    org: Organization,
    email: Optional[str],
    phone_digits: Optional[str],
    limit: int = _PAST_LIMIT,
) -> List[Tuple[Booking, Optional[datetime]]]:
    """Find recent past bookings for this org by email and/or phone."""
    return await _find_bookings_where(
        db, org, email, phone_digits, upcoming=False, limit=limit
    )


async def _find_bookings_where(
    db: AsyncSession,
    org: Organization,
    email: Optional[str],
    phone_digits: Optional[str],
    *,
    upcoming: bool,
    limit: int,
) -> List[Tuple[Booking, Optional[datetime]]]:
    if not email and not phone_digits:
        return []
    now = datetime.now(timezone.utc)
    if upcoming:
        stmt = (
            select(Booking, TimeSlot.start_time)
            .join(TimeSlot, Booking.slot_id == TimeSlot.id)
            .where(Booking.org_id == org.id, TimeSlot.start_time >= now)
            .order_by(TimeSlot.start_time.asc())
            .limit(limit * 2)
        )
    else:
        stmt = (
            select(Booking, TimeSlot.start_time)
            .join(TimeSlot, Booking.slot_id == TimeSlot.id)
            .where(Booking.org_id == org.id, TimeSlot.start_time < now)
            .order_by(TimeSlot.start_time.desc())
            .limit(limit * 2)
        )
    if email and phone_digits:
        digits_tail = str(phone_digits)[-10:]
        stmt = stmt.where(
            (func.lower(Booking.customer_email) == email.lower())
            | (Booking.customer_phone.ilike(f"%{digits_tail}%"))
        )
    elif email:
        stmt = stmt.where(func.lower(Booking.customer_email) == email.lower())
    else:
        digits_tail = str(phone_digits or "")[-10:]
        stmt = stmt.where(Booking.customer_phone.ilike(f"%{digits_tail}%"))
    rows = (await db.execute(stmt)).all()
    out: List[Tuple[Booking, Optional[datetime]]] = []
    for booking, start_time in rows:
        if phone_digits and not email:
            stored = re.sub(r"\D", "", str(booking.customer_phone or ""))
            if stored and not (
                stored == phone_digits or stored.endswith(phone_digits[-10:])
            ):
                continue
        out.append((booking, start_time))
        if len(out) >= limit:
            break
    return out


def _format_booking_line(
    booking: Booking, start_time: Optional[datetime], tz_name: str
) -> str:
    if not start_time:
        return "an upcoming appointment (time to be confirmed)"
    try:
        local = start_time.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = start_time
    hour_12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local.strftime('%A, %B %d')} at {hour_12}:{local.minute:02d} {ampm}"


async def handle_check_booking(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    contact_override: Optional[Tuple[str, str]] = None,
) -> ActionPayload:
    """LLM-triggered lookup: show the user their existing appointment(s).

    Full loop: if no contact is known yet, ask for it and mark the lookup
    pending — the next message carrying an email/phone completes it via
    `maybe_complete_pending_lookup`. Contact given for the lookup is stored
    into the session (it doubles as the booking identity), and booking
    progress (missing_fields / current_intent) is otherwise preserved.
    """
    session_id = chat_session.session_id
    collected = dict(chat_session.collected_data or {})
    if contact_override is not None:
        kind, value = contact_override
        if kind == "email" and not collected.get("email"):
            collected["email"] = value
        elif kind == "phone" and not collected.get("phone"):
            collected["phone"] = f"+{value}" if value.isdigit() else value
        collected.pop(CHECK_PENDING_KEY, None)
        chat_session.collected_data = collected
        _drop_satisfied_missing(chat_session, collected)
    email, phone_digits = _resolve_contact(collected)
    tz_name = _org_timezone_name(org_config)

    if not email and not phone_digits:
        collected[CHECK_PENDING_KEY] = True
        chat_session.collected_data = collected
        await _persist_state(db, chat_session)
        msg = (
            "I can look that up for you. "
            "Please share the email or phone number you booked with, "
            "and I'll fetch your appointment details."
        )
        action = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", field_meta=None
        )
        await _save_transcript(db, session_id, "", msg, "TEXT_INPUT", "CHECK_BOOKING")
        return action

    collected.pop(CHECK_PENDING_KEY, None)
    chat_session.collected_data = collected
    await _persist_state(db, chat_session)

    try:
        upcoming = await _find_bookings(db, org, email, phone_digits)
    except Exception:
        logger.warning("check-booking lookup failed", exc_info=True)
        upcoming = []
    try:
        past = await _find_past_bookings(db, org, email, phone_digits)
    except Exception:
        logger.warning("check-booking past lookup failed", exc_info=True)
        past = []

    resume = _resume_offer(chat_session)
    if not upcoming and not past:
        identity = email or (f"+{phone_digits}" if phone_digits else "those details")
        msg = (
            f"I checked, and you don't have any previous bookings under **{identity}**."
            f"{resume}"
        )
        action = ActionPayload(
            message=msg, widget_directive="TEXT_INPUT", field_meta=None
        )
        await _save_transcript(db, session_id, "", msg, "TEXT_INPUT", "CHECK_BOOKING")
        return action

    parts: List[str] = []
    if upcoming:
        header = (
            "Here's your upcoming appointment:"
            if len(upcoming) == 1
            else f"Here are your {len(upcoming)} upcoming appointments:"
        )
        parts.append(header)
        parts.extend(
            f"• **{_format_booking_line(b, st, tz_name)}**"
            + (f" — {b.customer_name}" if b.customer_name else "")
            for b, st in upcoming
        )
    if past:
        parts.append(
            "Your past appointment"
            + ("s:" if len(past) > 1 else ":")
        )
        parts.extend(
            f"• {_format_booking_line(b, st, tz_name)}"
            + (f" — {b.customer_name}" if b.customer_name else "")
            for b, st in past
        )
    msg = "\n".join(parts) + resume
    action = ActionPayload(message=msg, widget_directive="TEXT_INPUT", field_meta=None)
    await _save_transcript(db, session_id, "", msg, "TEXT_INPUT", "CHECK_BOOKING")
    return action


def is_check_pending(chat_session: ChatSessionState) -> bool:
    try:
        collected = chat_session.collected_data or {}
        return bool(isinstance(collected, dict) and collected.get(CHECK_PENDING_KEY))
    except Exception:
        return False


async def maybe_complete_pending_lookup(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
) -> Optional[ActionPayload]:
    """Complete a pending lookup when the message carries contact identity.

    Returns None when no lookup is pending or no contact is present, so the
    message flows into the normal booking pipeline untouched.
    """
    if not is_check_pending(chat_session):
        return None
    contact = _extract_contact(message_text)
    if contact is None:
        return None
    try:
        await crud_chat.save_message(db, chat_session.session_id, "user", message_text)
    except Exception:
        pass
    return await handle_check_booking(
        db, org, org_config, chat_session, contact_override=contact
    )


def _extract_contact(text: str) -> Optional[Tuple[str, str]]:
    """Find an email or phone in free text. No keyword lists — pure parsing."""
    try:
        from app.engine.cancel_flow import extract_contact_from_text

        return extract_contact_from_text(text)
    except Exception:
        return None


def _drop_satisfied_missing(
    chat_session: ChatSessionState, collected: Dict[str, Any]
) -> None:
    try:
        missing = list(chat_session.missing_fields or [])
        kept = [
            field
            for field in missing
            if not (collected.get(field) and str(collected.get(field)).strip())
        ]
        if len(kept) != len(missing):
            chat_session.missing_fields = kept
    except Exception:
        pass


def _resume_offer(chat_session: ChatSessionState) -> str:
    try:
        missing = list(chat_session.missing_fields or [])
        if missing:
            return (
                f"\n\nIf you'd like to make a new booking, just say the word"
                f" — next I'll need your {missing[0].replace('_', ' ')}."
            )
    except Exception:
        pass
    return ""


async def _persist_state(db: AsyncSession, chat_session: ChatSessionState) -> None:
    try:
        await crud_chat.update_chat_session(
            db, chat_session.session_id, chat_session
        )
    except Exception:
        logger.warning("check-booking state persist failed", exc_info=True)


async def _save_transcript(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    assistant_message: str,
    directive: str,
    intent: Optional[str] = None,
) -> None:
    try:
        if user_message:
            await crud_chat.save_message(db, session_id, "user", user_message)
        metadata: Dict[str, Any] = {"directive": directive}
        if intent:
            metadata["intent"] = intent
        await crud_chat.save_message(
            db, session_id, "agent", assistant_message, metadata=metadata
        )
    except Exception:
        logger.warning("Failed to persist check-booking transcript", exc_info=True)


_LLM_BYPASS_TIMEOUT_SECONDS = 10.0


async def should_bypass_step_guard(message_text: str) -> bool:
    """LLM decides whether a typed message is NOT a date/slot attempt.

    Used by WhatsApp SELECT_DATE / SELECT_SLOT guards so lookup, cancel,
    change, objection or question turns fall through to the engine instead
    of being trapped behind a date-format correction. No keyword lists —
    the model reads the actual meaning.

    Fails open to True (bypass to the engine) when the model cannot be
    reached. It used to fail closed, and that is what made the date step
    intermittently useless: with the LLM slow or down, "go on" and "still
    there?" were answered with "That doesn't look like a valid date ❌" and
    no dates at all. Telling someone their message is not a date when the
    check simply failed is worse than letting the engine decide, and the
    engine re-asks the same field for genuine gibberish anyway.
    """
    text = (message_text or "").strip()
    if not text:
        return False
    # A value that parses as a plausible date/time is always a step attempt.
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        from app.engine import message_processor as _mp

        prompt = (
            "You route a typed reply inside a booking widget that is currently "
            "asking the customer to pick a date or time.\n"
            f'USER_MESSAGE: "{text}"\n\n'
            "Is the user trying to provide a date/time value (answer YES), "
            "or is the user doing anything else — asking about an existing "
            "booking, cancelling, changing details, asking a question, "
            "greeting, or any other message (answer NO)?\n"
            'Return ONLY valid JSON: {"provides_datetime": true|false}'
        )
        import asyncio as _asyncio
        import json as _json

        raw = await _asyncio.wait_for(
            _mp.llm_engine._call_llm(
                prompt,
                system_prompt=(
                    "You are a strict booking-step router. Output JSON only."
                ),
                json_mode=True,
            ),
            timeout=_LLM_BYPASS_TIMEOUT_SECONDS,
        )
        payload = _json.loads(str(raw or ""))
        provides = payload.get("provides_datetime", True)
        # provides_datetime True -> it IS a date attempt -> do NOT bypass.
        return not bool(provides)
    except Exception:
        logger.warning("step-guard LLM bypass check failed, routing to engine", exc_info=True)
        return True
