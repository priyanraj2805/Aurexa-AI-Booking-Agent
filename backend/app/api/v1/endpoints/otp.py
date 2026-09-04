from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from app.api.v1.endpoints.auth import deny_impersonation
from app.db.database import get_db
from app.crud.otp import OTPGenerateRequest, OTPVerifyRequest, generate_otp, verify_otp
from app.core.config import settings
from app.core.limiter import limiter
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


class OTPGenerateResponse(BaseModel):
    status: str
    demo_code: Optional[str] = None  # Only populated in DEBUG mode


class OTPVerifyResponse(BaseModel):
    status: str
    message: str


@router.post("/generate", response_model=OTPGenerateResponse)
@limiter.limit("3/minute")
async def request_otp(
    request: Request,
    data: OTPGenerateRequest,
    _: None = Depends(deny_impersonation),
    db: AsyncSession = Depends(get_db)
):
    """Generates an OTP for the given session+channel. Email via SMTP, phone via Zavu SMS (kd78m7agx48rpyegpfrmhp5rsx8dm8t9)."""
    channel = (getattr(data, "channel", "email") or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"
    try:
        code = await generate_otp(db, str(data.session_id), channel=channel)
        logger.info(f"OTP ({channel}) generated for session {data.session_id}")
        try:
            if channel == "phone":
                # Phone OTP via Zavu sms_oneway — fetch phone from session and send SMS
                from sqlalchemy.future import select as _select2
                from app.models.session import ChatSession as _CS2
                import uuid as _uuid3

                sid3 = _uuid3.UUID(str(data.session_id))
                res3 = await db.execute(_select2(_CS2).where(_CS2.id == sid3))
                sess3 = res3.scalars().first()
                phone = ""
                if sess3 and isinstance(sess3.state_data, dict):
                    phone = str(sess3.state_data.get("phone") or "").strip()
                if phone:
                    from app.core.sms import send_phone_otp_sms

                    try:
                        await send_phone_otp_sms(phone, code)
                        logger.info(f"Zavu phone OTP sent to {phone[:6]}*** for session {data.session_id}")
                    except Exception as sms_err:
                        logger.error(f"Zavu SMS send failed for {data.session_id}: {sms_err}")
                        # Keep OTP record; client can retry via resend cooldown
                else:
                    logger.warning(f"Phone OTP generate: no phone in session {data.session_id}")
            else:
                # Email channel — fetch email and send via SMTP
                from sqlalchemy.future import select as _select
                from app.models.session import ChatSession as _CS
                import uuid as _uuid2
                sid2 = _uuid2.UUID(str(data.session_id))
                res2 = await db.execute(_select(_CS).where(_CS.id == sid2))
                sess2 = res2.scalars().first()
                email = ""
                if sess2 and isinstance(sess2.state_data, dict):
                    from app.crud import chat as _crud_chat
                    from app.engine.recipient_resolution import resolve_email_from_collected_data
                    snap = sess2.state_data.get(_crud_chat.FIELD_DEFINITIONS_SNAPSHOT_KEY)
                    defs = snap if isinstance(snap, list) else None
                    email = resolve_email_from_collected_data(sess2.state_data, defs)
                if email:
                    from app.core.mail import send_otp_email
                    org_id = getattr(sess2, "org_id", None)
                    if org_id:
                        await send_otp_email(email, code, org_id=org_id)
        except Exception as send_err:
            logger.error(f"Failed to deliver OTP ({channel}) for {data.session_id}: {send_err}")
        return OTPGenerateResponse(
            status="sent",
            demo_code=code if settings.DEBUG else None,
        )
    except Exception as e:
        logger.error(f"Error generating OTP: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/verify", response_model=OTPVerifyResponse)
@limiter.limit("5/minute")
async def submit_otp(
    request: Request,
    data: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """Verifies OTP. Both email and phone (Zavu SMS) use 6-digit code; phone also accepts legacy Firebase id_token."""
    channel = (getattr(data, "channel", "email") or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"
    id_token = getattr(data, "id_token", None)

    # --- Phone channel: Zavu 6-digit code primary, Firebase id_token fallback ---
    if channel == "phone":
        # Prefer Firebase if id_token supplied (legacy widget path)
        if id_token and str(id_token).strip():
            try:
                is_valid = await verify_otp(db, str(data.session_id), code=None, channel="phone", id_token=id_token)
                if is_valid:
                    logger.info(f"Phone OTP verified via Firebase for session {data.session_id}")
                    return OTPVerifyResponse(status="success", message="OTP Verified")
            except ValueError as e:
                msg = str(e)
                low = msg.lower()
                if any(k in low for k in ("expired", "invalid firebase", "revoked", "missing phone_number", "missing uid", "not configured")):
                    if "not configured" in low:
                        msg = msg + " (Check backend logs - FIREBASE_SERVICE_ACCOUNT_JSON missing)"
                    raise HTTPException(status_code=401, detail=msg)
                if "too many" in low or "cooldown" in low or "maximum resend" in low:
                    raise HTTPException(status_code=429, detail=msg)
                raise HTTPException(status_code=400, detail=msg)
            # Fall through to code path if token invalid
        # Zavu 6-digit code path
        if not data.code or not str(data.code).strip():
            raise HTTPException(status_code=400, detail="6-digit code required for phone verification (or Firebase id_token).")
        try:
            is_valid = await verify_otp(db, str(data.session_id), str(data.code).strip(), channel="phone", id_token=None)
        except ValueError as e:
            raise HTTPException(status_code=429, detail=str(e))
        if is_valid:
            logger.info(f"Phone OTP verified via Zavu code for session {data.session_id}")
            return OTPVerifyResponse(status="success", message="OTP Verified")
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code")

    # --- Email channel: 6-digit code ---
    try:
        is_valid = await verify_otp(db, str(data.session_id), data.code, channel="email", id_token=None)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    if is_valid:
        return OTPVerifyResponse(status="success", message="OTP Verified")
    else:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code")
