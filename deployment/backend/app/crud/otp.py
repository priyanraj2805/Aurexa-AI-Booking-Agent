import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, UUID4

from app.models.otp import OTPRecord
from app.models.session import ChatSession
from app.crud import chat as crud_chat
from app.engine.recipient_resolution import resolve_email_from_collected_data

MAX_OTP_ATTEMPTS = 5
OTP_COOLDOWN_SECONDS = 30
OTP_RESEND_CAP = 3
OTP_VALID_FOR_SECONDS = 300
_OTP_POLICY_KEY = "_otp_policy"


def _build_otp_context_key(session: ChatSession) -> str:
    state_data = session.state_data if isinstance(session.state_data, dict) else {}
    snapshot = state_data.get(crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY)
    snapshot_definitions = snapshot if isinstance(snapshot, list) else None
    email = resolve_email_from_collected_data(state_data, snapshot_definitions)
    date_value = _normalize_date_component(state_data.get("date"))
    time_value = _normalize_time_component(state_data.get("time"))
    return f"otp:{session.id}:{email}:{date_value}:{time_value}"


def _normalize_date_component(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return text


def _normalize_time_component(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%H:%M")
    except Exception:
        return text

class OTPGenerateRequest(BaseModel):
    session_id: UUID4

class OTPVerifyRequest(BaseModel):
    session_id: UUID4
    code: str

def _hash_code(code: str) -> str:
    """Simple SHA256 hash for the OTP code."""
    return hashlib.sha256(code.encode()).hexdigest()


def _parse_iso_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _get_policy_state(session: ChatSession) -> dict:
    if not isinstance(session.state_data, dict):
        return {}
    policy = session.state_data.get(_OTP_POLICY_KEY)
    if not isinstance(policy, dict):
        return {}
    return dict(policy)


def _policy_metadata_from_state(state: dict, *, now: datetime) -> dict:
    resend_count = int(state.get("resend_count") or 0)
    last_sent_at = _parse_iso_datetime(state.get("last_sent_at"))
    cooldown_remaining = 0
    if last_sent_at:
        elapsed = (now - last_sent_at).total_seconds()
        cooldown_remaining = max(0, int(OTP_COOLDOWN_SECONDS - elapsed))
    return {
        "otp_cooldown_seconds": OTP_COOLDOWN_SECONDS,
        "resend_cap": OTP_RESEND_CAP,
        "valid_for_seconds": OTP_VALID_FOR_SECONDS,
        "resends_left": max(0, OTP_RESEND_CAP - resend_count),
        "cooldown_remaining_seconds": cooldown_remaining,
    }


async def get_otp_policy_metadata(db: AsyncSession, session_id: str) -> dict:
    import uuid as _uuid

    sid = _uuid.UUID(session_id)
    result = await db.execute(select(ChatSession).where(ChatSession.id == sid))
    session = result.scalars().first()
    if not session:
        raise ValueError("Session not found")
    state = _get_policy_state(session)
    return _policy_metadata_from_state(state, now=datetime.now(timezone.utc))


async def generate_otp(
    db: AsyncSession,
    session_id: str,
    *,
    resend: bool = False,
) -> str:
    """Generates a 6-digit OTP, stores its hash, and returns the raw code."""
    import uuid as _uuid
    sid = _uuid.UUID(session_id)

    result = await db.execute(select(ChatSession).where(ChatSession.id == sid))
    session = result.scalars().first()
    if not session:
        raise ValueError("Session not found")

    now = datetime.now(timezone.utc)
    policy_state = _get_policy_state(session)
    resend_count = int(policy_state.get("resend_count") or 0)
    last_sent_at = _parse_iso_datetime(policy_state.get("last_sent_at"))

    if resend and last_sent_at:
        elapsed = (now - last_sent_at).total_seconds()
        if elapsed < OTP_COOLDOWN_SECONDS:
            raise ValueError("OTP resend cooldown active. Please wait before retrying.")
    if resend and resend_count >= OTP_RESEND_CAP:
        raise ValueError("Maximum resend attempts reached. Please try again later.")

    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = _hash_code(code)

    result = await db.execute(select(OTPRecord).where(OTPRecord.session_id == sid))
    existing_otp = result.scalars().first()

    expires = now + timedelta(seconds=OTP_VALID_FOR_SECONDS)

    if existing_otp:
        existing_otp.code_hash = code_hash
        existing_otp.expires_at = expires
        existing_otp.failed_attempts = 0
    else:
        new_otp = OTPRecord(
            session_id=sid,
            code_hash=code_hash,
            expires_at=expires
        )
        db.add(new_otp)

    if resend:
        resend_count += 1
    policy_state["resend_count"] = resend_count
    policy_state["last_sent_at"] = now.isoformat()
    session_state = dict(session.state_data) if isinstance(session.state_data, dict) else {}
    session_state[_OTP_POLICY_KEY] = policy_state
    session.state_data = session_state

    await db.commit()
    return code

async def verify_otp(db: AsyncSession, session_id: str, code: str) -> bool:
    """Verifies the OTP code with brute-force protection and row locking."""
    import uuid as _uuid
    sid = _uuid.UUID(session_id)

    # Lock the OTP row to prevent concurrent verification race
    # (with_for_update is Postgres-only; degrade gracefully on SQLite)
    stmt = select(OTPRecord).where(OTPRecord.session_id == sid)
    try:
        result = await db.execute(stmt.with_for_update(nowait=True))
    except Exception:
        result = await db.execute(stmt)
    otp_record = result.scalars().first()
    
    if not otp_record:
        return False
        
    # Check expiration (handle naive datetimes from SQLite)
    now = datetime.now(timezone.utc)
    expires = otp_record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        await db.delete(otp_record)
        session_res = await db.execute(select(ChatSession).where(ChatSession.id == sid))
        session = session_res.scalars().first()
        if session and isinstance(session.state_data, dict):
            session.state_data.pop(_OTP_POLICY_KEY, None)
        await db.commit()
        return False

    # Check brute-force lockout
    if otp_record.failed_attempts >= MAX_OTP_ATTEMPTS:
        await db.delete(otp_record)
        await db.commit()
        raise ValueError("Too many failed attempts. Please request a new code.")
        
    # Check hash
    if _hash_code(code) == otp_record.code_hash:
        # Success! Mark session as verified
        session_res = await db.execute(select(ChatSession).where(ChatSession.id == sid))
        session = session_res.scalars().first()
        if session:
            session.otp_verified = True
            session.current_intent = "CONFIRM_BOOKING"
            session_state = dict(session.state_data) if isinstance(session.state_data, dict) else {}
            session_state.pop(_OTP_POLICY_KEY, None)
            internal = session_state.get("_internal")
            if not isinstance(internal, dict):
                internal = {}
            internal["otp_verified_key"] = _build_otp_context_key(session)
            session_state["_internal"] = internal
            session.state_data = session_state
             
        # Clean up used OTP
        await db.delete(otp_record)
        await db.commit()
        return True
    
    # Failed attempt — increment counter
    otp_record.failed_attempts = (otp_record.failed_attempts or 0) + 1
    await db.commit()
    return False
