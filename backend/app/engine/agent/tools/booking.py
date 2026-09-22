import hashlib
import logging
import uuid
from typing import Any, Dict

from dateutil.parser import parse as date_parse
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.crud.booking import SLOT_LOCK_MINUTES, attempt_booking
from app.crud.chat import get_chat_session, update_chat_session
from app.engine.agent import tool_contracts
from app.engine.slot_engine import create_timeslot_lock

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY = "_booking_commit"


def _fingerprint(target_date: str, time: str, unit_id: str = "", provider_id: str = "") -> str:
    payload = f"{target_date}|{time}|{unit_id or ''}|{provider_id or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_success_message(
    target_date: str,
    time: str,
    idempotent_replay: bool = False,
    unit_name: str = "",
    provider_name: str = "",
    fee_display: str = "",
) -> str:
    message = f"SUCCESS: Booking confirmed for {target_date} at {time}."
    if provider_name or unit_name:
        message += f" ({', '.join(p for p in (provider_name, unit_name) if p)})"
    if fee_display:
        message += f" Fee: {fee_display}."
    if idempotent_replay:
        message += " (already confirmed)"
    return message


@tool
async def create_booking_record(target_date: str, time: str, config: RunnableConfig) -> str:
    """
    Creates the final booking record once the user confirms all details.
    Must ONLY be called after all required fields are collected and user agrees.
    """
    contract_result = tool_contracts.validate_booking_create_args(
        {"target_date": target_date, "time": time}
    )
    if not contract_result["ok"]:
        return str(contract_result["error"])

    normalized = contract_result["normalized"]
    normalized_date = normalized["target_date"]
    normalized_time = normalized["time"]

    db = config.get("configurable", {}).get("db")
    org_id_str = config.get("configurable", {}).get("org_id")
    session_id = config.get("configurable", {}).get("thread_id") or config.get(
        "configurable", {}
    ).get("session_id")

    if not db or not org_id_str or not session_id:
        return "System error: Could not access database context."

    try:
        chat_session = await get_chat_session(db, session_id)
        if not chat_session:
            return "System error: Session not found."

        # STRICT per-org OTP gate: book only when every admin-enabled channel
        # verifies. email=require_otp, phone=require_phone_otp, both, or none.
        try:
            from sqlalchemy.future import select as _select_gate
            from app.models.config import OrgConfig as _OrgConfigGate

            _cfg_res = await db.execute(
                _select_gate(_OrgConfigGate).where(
                    _OrgConfigGate.org_id == uuid.UUID(org_id_str)
                )
            )
            _org_cfg = _cfg_res.scalars().first()
            _raw_e = getattr(_org_cfg, "require_otp", True)
            _raw_p = getattr(_org_cfg, "require_phone_otp", False)
            _need_email = True if _raw_e is None else bool(_raw_e)
            _need_phone = False if _raw_p is None else bool(_raw_p)
            _email_ok = bool(getattr(chat_session, "otp_verified", False)) or not _need_email
            _phone_ok = bool(getattr(chat_session, "phone_otp_verified", False)) or not _need_phone
            if not (_email_ok and _phone_ok):
                _missing = []
                if _need_email and not getattr(chat_session, "otp_verified", False):
                    _missing.append("email OTP")
                if _need_phone and not getattr(chat_session, "phone_otp_verified", False):
                    _missing.append("phone OTP")
                return (
                    "BLOCKED: OTP verification required ("
                    + "+".join(_missing)
                    + "). Do NOT confirm the booking — ask the user to enter the verification code first."
                )
        except Exception as _gate_err:
            logger.error("Booking OTP gate check failed; blocking booking: %s", _gate_err)
            return "BLOCKED: Could not verify OTP status due to a system error. Do NOT confirm the booking."

        collected_data = (
            chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
        )
        # Service catalog selection (Phase 3): IDs land in collected_data via
        # the catalog selection step / widget. Absent = orgs without catalog.
        raw_unit_id = collected_data.get("unit_id") or ""
        raw_provider_id = collected_data.get("provider_id") or ""
        fingerprint = _fingerprint(normalized_date, normalized_time, str(raw_unit_id), str(raw_provider_id))
        existing_commit = collected_data.get(IDEMPOTENCY_KEY, {})
        if isinstance(existing_commit, dict) and existing_commit.get("fingerprint") == fingerprint:
            return _build_success_message(normalized_date, normalized_time, idempotent_replay=True)

        # Validate catalog selection BEFORE locking a slot — never persist a
        # booking with a foreign/stale unit or provider (C1-safe).
        from app.services.catalog_service import (
            resolve_effective_fee,
            validate_catalog_selection,
        )

        try:
            sel_unit, sel_provider = await validate_catalog_selection(
                db, uuid.UUID(org_id_str), raw_unit_id, raw_provider_id
            )
        except ValueError as catalog_err:
            return (
                f"NOT CONFIRMED: {catalog_err} "
                "Ask the customer to choose again from the current list — do NOT confirm."
            )
        fee_paise, fee_currency = await resolve_effective_fee(
            db, uuid.UUID(org_id_str), raw_unit_id, raw_provider_id
        )

        parsed_date = date_parse(normalized_date).date()
        requested_time = date_parse(normalized_time).time()

        slot = await create_timeslot_lock(
            db=db,
            org_id=uuid.UUID(org_id_str),
            target_date=parsed_date,
            requested_time=requested_time,
            duration_minutes=0,
            lock_minutes=SLOT_LOCK_MINUTES,
        )
        if not slot:
            return "Booking failed: this slot was already taken."

        customer_name = collected_data.get("name", "Unknown")
        customer_email = collected_data.get("email", "unknown@example.com")
        customer_phone = collected_data.get("phone", "")

        booking = await attempt_booking(
            db=db,
            session_id=session_id,
            org_id=org_id_str,
            slot_id=str(slot.id),
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            user_timezone=getattr(chat_session, "user_timezone", None),
            unit_id=str(raw_unit_id) if raw_unit_id else None,
            provider_id=str(raw_provider_id) if raw_provider_id else None,
            amount_paise=fee_paise,
            currency=fee_currency,
        )

        collected_data[IDEMPOTENCY_KEY] = {
            "fingerprint": fingerprint,
            "booking_id": str(getattr(booking, "id", "")),
            "target_date": normalized_date,
            "time": normalized_time,
        }
        chat_session.collected_data = collected_data
        await update_chat_session(db, session_id, chat_session)

        from app.core.org_notify import notify_org

        await notify_org(
            org_id=uuid.UUID(org_id_str),
            kind="booking_created",
            title="New appointment booked",
            body=f"{customer_name} booked {normalized_date} at {normalized_time}.",
            resource_type="booking",
            resource_id=str(getattr(booking, "id", "")),
        )

        fee_display = ""
        if fee_paise:
            fee_display = f"{fee_currency or 'INR'} {(fee_paise / 100):,.2f}"
        return _build_success_message(
            normalized_date,
            normalized_time,
            unit_name=getattr(sel_unit, "name", "") or "",
            provider_name=getattr(sel_provider, "full_name", "") or "",
            fee_display=fee_display,
        )
    except Exception as error:
        logger.error("Booking error: %s", error)
        return f"Booking failed due to a system error: {error}"
