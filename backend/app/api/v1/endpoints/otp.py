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
    """Generates an OTP for the given session+channel. Email via SMTP, phone via WhatsApp."""
    from app.core.otp_delivery import delivery_error, delivery_succeeded
    from app.crud.otp import invalidate_otp_delivery

    channel = (getattr(data, "channel", "email") or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"
    try:
        code = await generate_otp(db, str(data.session_id), channel=channel)
        logger.info(f"OTP ({channel}) generated for session {data.session_id}")
        try:
            if channel == "phone":
                # Phone OTP via provider abstraction (WhatsApp active, SMS fallback — both use same OTPRecord)
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
                    from app.core.config import settings as _cfg
                    from app.core.otp_delivery.whatsapp_provider import WhatsAppProvider
                    provider = WhatsAppProvider()
                    result = await provider.send_otp(phone, code, str(data.session_id))
                    if not delivery_succeeded(
                        result,
                        allow_mock=bool(getattr(_cfg, "OTP_ALLOW_MOCK_DELIVERY", False)),
                    ):
                        await invalidate_otp_delivery(db, str(data.session_id), "phone")
                        raise RuntimeError(delivery_error(result))
                    logger.info(f"Phone OTP ({provider.__class__.__name__}) accepted for session {data.session_id}")
                else:
                    await invalidate_otp_delivery(db, str(data.session_id), "phone")
                    raise ValueError("Phone number is required")
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
                        result = await send_otp_email(email, code, org_id=org_id)
                        if not delivery_succeeded(
                            result,
                            allow_mock=bool(getattr(settings, "OTP_ALLOW_MOCK_DELIVERY", False)),
                        ):
                            await invalidate_otp_delivery(db, str(data.session_id), "email")
                            raise RuntimeError(delivery_error(result))
                else:
                    await invalidate_otp_delivery(db, str(data.session_id), "email")
                    raise ValueError("Email address is required")
        except Exception as send_err:
            logger.error(f"Failed to deliver OTP ({channel}) for {data.session_id}: {send_err}")
            raise HTTPException(status_code=502, detail="OTP delivery failed") from send_err
        return OTPGenerateResponse(
            status="sent",
            demo_code=code if settings.DEBUG else None,
        )
    except HTTPException:
        raise
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
    """Verifies OTP. Both email and phone (WhatsApp) use a 6-digit code."""
    channel = (getattr(data, "channel", "email") or "email").lower()
    if channel not in ("email", "phone"):
        channel = "email"

    # --- Phone channel: WhatsApp 6-digit code ---
    if channel == "phone":
        if not data.code or not str(data.code).strip():
            raise HTTPException(status_code=400, detail="6-digit code required for phone verification.")
        try:
            is_valid = await verify_otp(db, str(data.session_id), str(data.code).strip(), channel="phone")
        except ValueError as e:
            raise HTTPException(status_code=429, detail=str(e))
        if is_valid:
            logger.info(f"Phone OTP verified via WhatsApp code for session {data.session_id}")
            return OTPVerifyResponse(status="success", message="OTP Verified")
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code")

    # --- Email channel: 6-digit code ---
    try:
        is_valid = await verify_otp(db, str(data.session_id), data.code, channel="email")
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    if is_valid:
        return OTPVerifyResponse(status="success", message="OTP Verified")
    else:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code")
