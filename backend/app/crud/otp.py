import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
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
OTP_VALID_FOR_SECONDS = 900
_OTP_POLICY_KEY = "_otp_policy"
_OTP_PHONE_POLICY_KEY = "_otp_phone_policy"

OTP_CHANNEL = Literal["email", "phone"]

async def verify_firebase_id_token(id_token: str, expected_phone: str) -> bool:
    """Legacy REST verifier - DEPRECATED. Kept for backward compat, delegates to Admin SDK.

    New code should use app.core.firebase.verify_firebase_id_token_admin.
    """
    from app.core.firebase import verify_firebase_id_token_admin

    try:
        decoded = await verify_firebase_id_token_admin(id_token)
        token_phone = decoded.get("phone_number", "")
        import phonenumbers

        def norm(p):
            try:
                pn = phonenumbers.parse(p, None)
                return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)
            except Exception:
                return p.replace(" ", "").replace("-", "")

        return norm(token_phone) == norm(expected_phone)
    except Exception:
        return False


def _normalize_phone(p: str) -> str:
    """Normalize phone to E.164 where possible, fallback to stripped."""
    if not p:
        return ""
    try:
        import phonenumbers

        pn = phonenumbers.parse(p, None)
        return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        return p.replace(" ", "").replace("-", "")


def _channel_policy_key(channel: str) -> str:
    return _OTP_PHONE_POLICY_KEY if channel == "phone" else _OTP_POLICY_KEY


def _build_otp_context_key(session: ChatSession) -> str:
    state_data = session.state_data if isinstance(session.state_data, dict) else {}
    snapshot = state_data.get(crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY)
    snapshot_definitions = snapshot if isinstance(snapshot, list) else None
    email = resolve_email_from_collected_data(state_data, snapshot_definitions)
    date_value = _normalize_date_component(state_data.get("date"))
    time_value = _normalize_time_component(state_data.get("time"))
    return f"otp:{session.id}:{email}:{date_value}:{time_value}"


def _build_phone_otp_context_key(session: ChatSession) -> str:
    state_data = session.state_data if isinstance(session.state_data, dict) else {}
    phone = str(state_data.get("phone") or "").strip()
    date_value = _normalize_date_component(state_data.get("date"))
    time_value = _normalize_time_component(state_data.get("time"))
    return f"otp_phone:{session.id}:{phone}:{date_value}:{time_value}"


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
    channel: OTP_CHANNEL = "email"

class OTPVerifyRequest(BaseModel):
    session_id: UUID4
    code: Optional[str] = None
    channel: OTP_CHANNEL = "email"
    id_token: Optional[str] = None  # Firebase ID token for phone channel

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


def _get_policy_state(session: ChatSession, channel: str = "email") -> dict:
    if not isinstance(session.state_data, dict):
        return {}
    key = _channel_policy_key(channel)
    policy = session.state_data.get(key)
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


async def get_otp_policy_metadata(db: AsyncSession, session_id: str, channel: str = "email") -> dict:
    import uuid as _uuid

    sid = _uuid.UUID(session_id)
    result = await db.execute(select(ChatSession).where(ChatSession.id == sid))
    session = result.scalars().first()
    if not session:
        raise ValueError("Session not found")
    state = _get_policy_state(session, channel)
    meta = _policy_metadata_from_state(state, now=datetime.now(timezone.utc))
    # Include channel for widget to know which OTP is requested
    meta["channel"] = channel
    return meta


async def generate_otp(
    db: AsyncSession,
    session_id: str,
    *,
    channel: str = "email",
    resend: bool = False,
) -> str:
    """Generates a 6-digit OTP for given session+channel, stores its hash, returns raw code.

    For channel=phone (Firebase), no OTPRecord is created - Firebase handles SMS.
    Rate limiting / cooldown / resend cap are still enforced via session state.
    """
    import uuid as _uuid
    channel = (channel or "email").lower().strip()
    if channel not in ("email", "phone"):
        channel = "email"
    sid = _uuid.UUID(session_id)

    result = await db.execute(select(ChatSession).where(ChatSession.id == sid))
    session = result.scalars().first()
    if not session:
        raise ValueError("Session not found")

    now = datetime.now(timezone.utc)
    policy_state = _get_policy_state(session, channel)
    resend_count = int(policy_state.get("resend_count") or 0)
    last_sent_at = _parse_iso_datetime(policy_state.get("last_sent_at"))

    if resend and last_sent_at:
        elapsed = (now - last_sent_at).total_seconds()
        if elapsed < OTP_COOLDOWN_SECONDS:
            remaining = max(1, int(OTP_COOLDOWN_SECONDS - elapsed))
            raise ValueError(
                f"OTP resend cooldown active. Please wait {remaining}s before retrying."
            )
    if resend and resend_count >= OTP_RESEND_CAP:
        raise ValueError("Maximum resend attempts reached. Please try again later.")

    # --- Zavu phone channel: create OTPRecord like email (BYO-OTP) ---
    # Firebase path deprecated — Zavu sms_oneway (kd78m7agx48rpyegpfrmhp5rsx8dm8t9) now delivers code
    # Fall through to shared 6-digit generation below for channel=="phone" as well

    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = _hash_code(code)

    result = await db.execute(select(OTPRecord).where(OTPRecord.session_id == sid, OTPRecord.channel == channel))
    existing_otp = result.scalars().first()

    expires = now + timedelta(seconds=OTP_VALID_FOR_SECONDS)

    # Persist OTPRecord first, before touching chat_sessions.state_data (which can be 14KB duplicated)
    # so the active OTP is available to verify_otp() immediately even if session flush is large
    if existing_otp:
        existing_otp.code_hash = code_hash
        existing_otp.expires_at = expires
        existing_otp.failed_attempts = 0
        otp_record_id = str(existing_otp.id) if hasattr(existing_otp, "id") else str(sid)
    else:
        new_otp = OTPRecord(
            session_id=sid,
            channel=channel,
            code_hash=code_hash,
            expires_at=expires
        )
        db.add(new_otp)
        await db.flush()
        otp_record_id = str(new_otp.id)

    # Safe correlation log (no OTP value)
    try:
        import logging as _lg
        _lg.getLogger(__name__).info(
            "otp.generated",
            extra={
                "event": "otp.generated",
                "otp_request_id": f"{sid}:{channel}:{now.isoformat()}",
                "session_id": str(sid),
                "channel": channel,
                "otp_record_id": otp_record_id,
                "created_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "resend": bool(resend),
                "status": "active",
            },
        )
    except Exception:
        pass

    await db.flush()
    await db.commit()  # commit OTPRecord alone so it persists even if session state_data is huge/corrupted
    # Keep _otp_*_policy shallow, never duplicate runtime_checkpoint inside collected_data
    if resend:
        resend_count += 1
    policy_state["resend_count"] = resend_count
    policy_state["last_sent_at"] = now.isoformat()
    session_state = dict(session.state_data) if isinstance(session.state_data, dict) else {}
    # Strip duplicated runtime_checkpoint from collected_data/state_data before persisting
    try:
        if isinstance(session_state.get("collected_data"), dict) and "runtime_checkpoint" in session_state["collected_data"]:
            session_state["collected_data"] = {k: v for k, v in session_state["collected_data"].items() if k != "runtime_checkpoint"}
        if "runtime_checkpoint" in session_state and isinstance(session_state.get("collected_data"), dict):
            # keep runtime_checkpoint only at top level, not inside collected_data
            pass
    except Exception:
        pass
    session_state[_channel_policy_key(channel)] = policy_state
    session.state_data = session_state

    await db.commit()
    return code

async def verify_otp(db: AsyncSession, session_id: str, code: Optional[str] = None, channel: str = "email", id_token: Optional[str] = None) -> bool:
    """Verifies OTP. Email and Phone (Zavu SMS) both use 6-digit OTPRecord.

    Phone channel now uses Zavu sms_oneway (kd78m7agx48rpyegpfrmhp5rsx8dm8t9) BYO-OTP:
    - Generate: 6-digit code stored hashed, expires 900s
    - Verify: hash compare, then phone_otp_verified=True
    Firebase id_token path kept as fallback if caller supplies it (legacy).
    """
    channel = (channel or "email").lower().strip()
    if channel not in ("email", "phone"):
        channel = "email"

    # --- Firebase fallback: if phone channel called with id_token, keep legacy verify (exclusive) ---
    if channel == "phone" and id_token and str(id_token).strip():
        # Legacy Firebase path — exclusive when id_token supplied (no fallback to code)
        import uuid as _uuid2

        sid2 = _uuid2.UUID(session_id)
        res2 = await db.execute(select(ChatSession).where(ChatSession.id == sid2))
        sess2 = res2.scalars().first()
        if not sess2:
            return False
        expected_phone = str(sess2.state_data.get("phone") or "").strip() if isinstance(sess2.state_data, dict) else ""
        from app.core.firebase import verify_firebase_id_token_admin

        decoded = await verify_firebase_id_token_admin(id_token)  # may raise ValueError -> caller maps to 401
        token_phone = decoded.get("phone_number") or ""
        norm_expected = _normalize_phone(expected_phone)
        norm_token = _normalize_phone(token_phone)
        if norm_token != norm_expected:
            return False  # mismatch -> endpoint returns 400 mismatch
        sess2.phone_otp_verified = True
        state2 = dict(sess2.state_data) if isinstance(sess2.state_data, dict) else {}
        state2.pop(_channel_policy_key("phone"), None)
        internal2 = state2.get("_internal")
        if not isinstance(internal2, dict):
            internal2 = {}
        internal2["phone_otp_verified_key"] = _build_phone_otp_context_key(sess2)
        state2["_internal"] = internal2
        sess2.state_data = state2
        await db.commit()
        return True
    import uuid as _uuid
    channel = (channel or "email").lower().strip()
    if channel not in ("email", "phone"):
        channel = "email"
    sid = _uuid.UUID(session_id)

    stmt = select(OTPRecord).where(OTPRecord.session_id == sid, OTPRecord.channel == channel)
    try:
        result = await db.execute(stmt.with_for_update(nowait=True))
    except Exception:
        result = await db.execute(stmt)
    otp_record = result.scalars().first()
    
    if not otp_record:
        return False
        
    now = datetime.now(timezone.utc)
    expires = otp_record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        await db.delete(otp_record)
        session_res = await db.execute(select(ChatSession).where(ChatSession.id == sid))
        session = session_res.scalars().first()
        if session and isinstance(session.state_data, dict):
            session.state_data.pop(_channel_policy_key(channel), None)
        await db.commit()
        return False

    if otp_record.failed_attempts >= MAX_OTP_ATTEMPTS:
        await db.delete(otp_record)
        await db.commit()
        raise ValueError("Too many failed attempts. Please request a new code.")
    
    if not code:
        return False
        
    if _hash_code(code) == otp_record.code_hash:
        session_res = await db.execute(select(ChatSession).where(ChatSession.id == sid))
        session = session_res.scalars().first()
        if session:
            session_state = dict(session.state_data) if isinstance(session.state_data, dict) else {}
            cancel_flow = session_state.get("_cancel_flow")
            is_cancel_flow = (
                isinstance(cancel_flow, dict) and cancel_flow.get("stage") == "otp"
            )
            if is_cancel_flow:
                cancel_flow["otp_verified"] = True
                session_state["_cancel_flow"] = cancel_flow
                session_state.pop(_channel_policy_key(channel), None)
                session.state_data = session_state
            else:
                if channel == "phone":
                    # Phone channel verification
                    session.phone_otp_verified = True
                    session_state.pop(_channel_policy_key(channel), None)
                    internal = session_state.get("_internal")
                    if not isinstance(internal, dict):
                        internal = {}
                    internal["phone_otp_verified_key"] = _build_phone_otp_context_key(session)
                    session_state["_internal"] = internal
                    session.state_data = session_state
                    # If email also required, keep intent pending till both verified
                    # Otherwise mark CONFIRM_BOOKING
                else:
                    session.otp_verified = True
                    session.current_intent = "CONFIRM_BOOKING"
                    session_state.pop(_channel_policy_key(channel), None)
                    internal = session_state.get("_internal")
                    if not isinstance(internal, dict):
                        internal = {}
                    internal["otp_verified_key"] = _build_otp_context_key(session)
                    session_state["_internal"] = internal
                    session.state_data = session_state
             
        await db.delete(otp_record)
        await db.commit()
        return True
    
    otp_record.failed_attempts = (otp_record.failed_attempts or 0) + 1
    await db.commit()
    return False
