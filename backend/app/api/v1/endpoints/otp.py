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
    """Generates an OTP for the given session. In production this would SMS the user."""
    try:
        code = await generate_otp(db, str(data.session_id))
        logger.info(f"OTP generated for session {data.session_id}")
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
    """Verifies the submitted action-hash code."""
    try:
        is_valid = await verify_otp(db, str(data.session_id), data.code)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    if is_valid:
        return OTPVerifyResponse(status="success", message="OTP Verified")
    else:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code")
