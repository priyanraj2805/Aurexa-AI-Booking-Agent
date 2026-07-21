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


def _fingerprint(target_date: str, time: str) -> str:
    payload = f"{target_date}|{time}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_success_message(target_date: str, time: str, idempotent_replay: bool = False) -> str:
    message = f"SUCCESS: Booking confirmed for {target_date} at {time}."
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

        collected_data = (
            chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
        )
        fingerprint = _fingerprint(normalized_date, normalized_time)
        existing_commit = collected_data.get(IDEMPOTENCY_KEY, {})
        if isinstance(existing_commit, dict) and existing_commit.get("fingerprint") == fingerprint:
            return _build_success_message(normalized_date, normalized_time, idempotent_replay=True)

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
        )

        collected_data[IDEMPOTENCY_KEY] = {
            "fingerprint": fingerprint,
            "booking_id": str(getattr(booking, "id", "")),
            "target_date": normalized_date,
            "time": normalized_time,
        }
        chat_session.collected_data = collected_data
        await update_chat_session(db, session_id, chat_session)
        return _build_success_message(normalized_date, normalized_time)
    except Exception as error:
        logger.error("Booking error: %s", error)
        return f"Booking failed due to a system error: {error}"
