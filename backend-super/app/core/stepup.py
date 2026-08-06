"""Shared MFA step-up guard (Phase 8).

A step-up token proves the admin re-verified their TOTP within the last
STEPUP_TOKEN_EXPIRE_MINUTES. Required for sensitive actions: refunds,
org deletion, impersonation.
"""
from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.rbac import CurrentAdmin
from app.core.security import PURPOSE_STEPUP, decode_token


def require_stepup(request: Request, admin: CurrentAdmin) -> None:
    token = request.cookies.get(settings.SUPER_ADMIN_STEPUP_COOKIE_NAME, "")
    payload = decode_token(token or "", expected_purpose=PURPOSE_STEPUP)
    if not payload or payload.get("sub") != str(admin.id):
        raise HTTPException(
            status_code=403,
            detail="MFA step-up required: re-verify your code via /auth/mfa/stepup",
        )
