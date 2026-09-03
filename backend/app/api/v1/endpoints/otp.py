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
    """Generates an OTP for the given session+channel. Email uses SMTP, phone uses Firebase (widget sends SMS)."""
    channel = (getattr(data, "channel", "email") or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"
    try:
        code = await generate_otp(db, str(data.session_id), channel=channel)
        logger.info(f"OTP ({channel}) generated for session {data.session_id}")
        # Dispatch to correct delivery channel
        # Email: send via SMTP. Phone: widget sends via Firebase, so no SMS sent here.
        # For phone (Firebase), code is "" and no DB record is created - do not expose demo_code.
        try:
            if channel == "phone":
                # Phone OTP: widget handles SMS via Firebase, no backend SMS send needed
                logger.info(f"Phone OTP generated for session {data.session_id} — widget will send via Firebase")
                # Do not leak demo code for phone; Firebase handles it
                return OTPGenerateResponse(status="sent", demo_code=None)
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
    """Verifies OTP. Email uses 6-digit code; Phone uses Firebase ID token only.

    Frontend already verifies OTP via Firebase JS SDK (confirmationResult.confirm + getIdToken)
    and sends Firebase ID token. Backend MUST verify that token via Firebase Admin SDK,
    extract phone_number, compare with session's stored phone, and mark verified.
    Raw 6-digit code validation is disabled for phone channel.
    """
    channel = (getattr(data, "channel", "email") or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"
    id_token = getattr(data, "id_token", None)

    # --- Phone channel: Firebase only, no 6-digit fallback ---
    if channel == "phone":
        if not id_token or not str(id_token).strip():
            raise HTTPException(status_code=400, detail="Firebase ID token required for phone verification. Ensure widget sent id_token after Firebase confirm.")
        try:
            is_valid = await verify_otp(db, str(data.session_id), code=None, channel="phone", id_token=id_token)
        except ValueError as e:
            msg = str(e)
            low = msg.lower()
            # Map Firebase auth errors to 401 so widget can show them distinctly
            if any(k in low for k in ("expired", "invalid firebase", "revoked", "missing phone_number", "missing uid", "not configured")):
                # Include actionable hint for the not-configured case
                if "not configured" in low:
                    msg = msg + " (Check backend logs - FIREBASE_SERVICE_ACCOUNT_JSON missing in backend/.env.docker)"
                raise HTTPException(status_code=401, detail=msg)
            if "too many" in low or "cooldown" in low or "maximum resend" in low:
                raise HTTPException(status_code=429, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        if is_valid:
            logger.info(f"Phone OTP verified for session {data.session_id}")
            return OTPVerifyResponse(status="success", message="OTP Verified")
        # Token valid but phone mismatch - include expected vs token for debugging (masked)
        logger.warning(f"Phone number mismatch for session {data.session_id}: session phone vs token phone differ")
        raise HTTPException(status_code=400, detail="Phone number mismatch - the verified number does not match the booking phone. Please re-enter phone correctly.")

    # --- Email channel: 6-digit code ---
    try:
        is_valid = await verify_otp(db, str(data.session_id), data.code, channel="email", id_token=None)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    if is_valid:
        return OTPVerifyResponse(status="success", message="OTP Verified")
    else:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code")
