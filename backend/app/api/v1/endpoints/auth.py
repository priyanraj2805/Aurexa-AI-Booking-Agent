import logging
import traceback
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import Header, Cookie
from typing import Optional, Literal
from app.db.database import get_db
from app.crud.user import get_user_by_email, create_user_with_org, update_user_password
from app.crud.auth_otp import (
    generate_auth_otp,
    verify_auth_otp,
    OTPError,
    OTP_PURPOSES,
)
from app.schemas.auth import (
    UserRegister,
    UserLogin,
    UserResponse,
    TokenResponse,
    OTPSendRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
)
from app.core.security import create_access_token, create_impersonation_token, decode_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, IMPERSONATION_TOKEN_EXPIRE_MINUTES, decode_impersonation_handshake, verify_password
from app.models.user import User
from app.models.organization import Organization
from app.core.config import settings
from app.core.limiter import limiter
from app.core.audit import log_audit_event
from app.schemas.audit import AuditAction
from app.core.mail import send_auth_otp_email

router = APIRouter()
logger = logging.getLogger(__name__)


def _support_service_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Support-Service-Key": settings.SUPPORT_SERVICE_KEY,
    }


def _set_auth_cookie(response: JSONResponse, token: str) -> JSONResponse:
    """Sets the JWT as an HttpOnly cookie on the response."""
    response.set_cookie(
        key="ds_token",
        value=token,
        httponly=True,
        secure=True,  # Must be True for SameSite=None
        samesite="none", # Allow cross-origin requests from frontend tunnel
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return response


# ── Dependency: current user from JWT ─────────────

async def get_current_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
    ds_token: Optional[str] = Cookie(None),
) -> User:
    # Try Bearer header first, then fall back to HttpOnly cookie
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif ds_token:
        token = ds_token

    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_email = payload["sub"]

    # Phase 8: impersonation tokens must map to a live impersonation session.
    if payload.get("imp"):
        jti = payload.get("jti", "")
        if not jti:
            raise HTTPException(status_code=401, detail="Invalid impersonation token")
        session = (
            await db.execute(
                text(
                    """
                    SELECT 1 FROM impersonation_sessions
                    WHERE token_jti = :jti
                      AND revoked_at IS NULL
                      AND expires_at > now()
                    """
                ),
                {"jti": jti},
            )
        ).scalar()
        if not session:
            raise HTTPException(
                status_code=401,
                detail="Impersonation session revoked or expired",
            )

    result = await db.execute(select(User).where(User.email == user_email))
    user = result.scalars().first()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


# ── Endpoints ─────────────────────────────────────

@router.post("/otp/send")
@limiter.limit("5/minute")
async def send_verification_otp(
    request: Request,
    data: OTPSendRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Email a 6-digit OTP for account registration (purpose="register").

    The email must NOT already have an account — login itself is a plain
    email + password flow without any OTP.
    """
    email = data.email.lower().strip()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required")

    existing = await get_user_by_email(db, email)
    if existing:
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        )

    try:
        code = await generate_auth_otp(db, email, "register", resend=False)
    except OTPError as e:
        raise HTTPException(status_code=429, detail=str(e))

    background_tasks.add_task(send_auth_otp_email, email, code, "register")
    return {"detail": "Verification code sent", "email": email, "purpose": "register"}


@router.post("/otp/resend")
@limiter.limit("5/minute")
async def resend_verification_otp(
    request: Request,
    data: OTPSendRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Resend a registration OTP for the same email (cooldown + cap enforced)."""
    email = data.email.lower().strip()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required")

    try:
        code = await generate_auth_otp(db, email, "register", resend=True)
    except OTPError as e:
        raise HTTPException(status_code=429, detail=str(e))

    background_tasks.add_task(send_auth_otp_email, email, code, "register")
    return {"detail": "Verification code resent", "email": email, "purpose": "register"}


@router.post("/register", response_model=TokenResponse)
@limiter.limit("5/minute")
async def register(
    request: Request,
    data: UserRegister,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account + organization (email-OTP verified)."""
    try:
        email = data.email.lower().strip()
        existing = await get_user_by_email(db, email)
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists")

        try:
            otp_ok = await verify_auth_otp(db, email, "register", data.otp_code)
        except OTPError as e:
            raise HTTPException(status_code=429, detail=str(e))
        if not otp_ok:
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired verification code. Request a new code and try again.",
            )

        user, org = await create_user_with_org(db, data)
        log_audit_event(
            background_tasks,
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            action=AuditAction.AUTH_REGISTER,
            resource_type="user",
            resource_id=str(user.id),
            old_values=None,
            new_values={"email": user.email},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        token = create_access_token(data={"sub": user.email, "org_id": str(user.org_id)})

        body = TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                org_id=user.org_id,
                org_name=org.name,
                avatar_url=getattr(user, "avatar_url", None),
                is_active=user.is_active,
                created_at=user.created_at,
            ),
        )
                # Deliberately NO auth cookie here: a fresh registration must not
        # create a session — the user signs in explicitly afterwards.
        return JSONResponse(content=body.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    data: UserLogin,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Sign in with email + password and issue the session token directly.

    No email OTP step on login — the verification code is only required
    when creating a new account.
    """
    try:
        email = data.email.lower().strip()
        user = await get_user_by_email(db, email)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not verify_password(data.password, user.hashed_password):
            log_audit_event(
                background_tasks,
                org_id=user.org_id,
                actor_id=user.id,
                actor_email=user.email,
                action=AuditAction.AUTH_LOGIN_FAILED,
                resource_type="user",
                resource_id=str(user.id),
                old_values={"event": "login_attempt"},
                new_values={"result": "failed_invalid_credentials"},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
            raise HTTPException(status_code=401, detail="Wrong password. Please try again.")

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Your account is disabled.")

        result = await db.execute(select(Organization).where(Organization.id == user.org_id))
        org = result.scalars().first()

        token = create_access_token(data={"sub": user.email, "org_id": str(user.org_id)})
        log_audit_event(
            background_tasks,
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            action=AuditAction.AUTH_LOGIN,
            resource_type="user",
            resource_id=str(user.id),
            old_values=None,
            new_values={"event": "login_success"},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        body = TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                org_id=user.org_id,
                org_name=org.name if org else None,
                avatar_url=getattr(user, "avatar_url", None),
                is_active=user.is_active,
                created_at=user.created_at,
            ),
        )
        response = JSONResponse(content=body.model_dump(mode="json"))
        return _set_auth_cookie(response, token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")


@router.post("/password/forgot")
@limiter.limit("5/hour")
async def forgot_password(
    request: Request,
    data: PasswordForgotRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Email a password-reset OTP if the account exists.

    Always returns the same generic response (whether or not the email is
    registered) so the endpoint cannot be used to enumerate accounts.
    """
    email = data.email.lower().strip()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required")

    user = await get_user_by_email(db, email)
    if user:
        try:
            code = await generate_auth_otp(db, email, "password_reset", resend=False)
        except OTPError as e:
            raise HTTPException(status_code=429, detail=str(e))
        background_tasks.add_task(send_auth_otp_email, email, code, "password_reset")

    return {"detail": "If an account exists for this email, a reset code has been sent."}


@router.post("/password/reset")
@limiter.limit("10/hour")
async def reset_password(
    request: Request,
    data: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Verify the emailed reset OTP, then set a new password for the account."""
    email = data.email.lower().strip()
    user = await get_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=404, detail="No account found for this email.")

    try:
        otp_ok = await verify_auth_otp(db, email, "password_reset", data.otp_code)
    except OTPError as e:
        raise HTTPException(status_code=429, detail=str(e))
    if not otp_ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset code. Request a new code and try again.",
        )

    if verify_password(data.new_password, user.hashed_password):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from your current password.",
        )

    await update_user_password(db, user, data.new_password)
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.AUTH_PASSWORD_RESET,
        resource_type="user",
        resource_id=str(user.id),
        old_values=None,
        new_values={"event": "password_reset"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"detail": "Password updated. You can now sign in with your new password."}


@router.get("/me", response_model=UserResponse)
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the currently authenticated user."""
    result = await db.execute(select(Organization).where(Organization.id == user.org_id))
    org = result.scalars().first()

    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        org_id=user.org_id,
        org_name=org.name if org else None,
        avatar_url=getattr(user, "avatar_url", None),
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/logout")
async def logout(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    """Clear the HttpOnly auth cookie."""
    log_audit_event(
        background_tasks,
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.AUTH_LOGOUT,
        resource_type="user",
        resource_id=str(user.id),
        old_values={"event": "session_active"},
        new_values={"event": "logged_out"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse(content={"detail": "Logged out"})
    response.delete_cookie(key="ds_token", path="/")
    return response


# ── Phase 11b: impersonation hand-off (admin console) ──────────────────


async def deny_impersonation(
    authorization: Optional[str] = Header(None),
    ds_token: Optional[str] = Cookie(None),
) -> None:
    """Reject sensitive actions while the session is an impersonation one.

    Used as a dependency on destructive endpoints (password/MFA resets,
    billing, deletions) so an impersonated session is read-mostly.
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif ds_token:
        token = ds_token
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    payload = decode_access_token(token)
    if payload and payload.get("imp"):
        raise HTTPException(
            status_code=403,
            detail="This action is not allowed while impersonating a user",
        )


@router.post("/impersonate/enter")
async def impersonate_enter(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a control-plane handshake ticket for a tenant session cookie.

    The super admin clicks "Open console" in the control plane, landing on
    /impersonate/enter?hs=<handshake>. This endpoint validates the handshake
    (signed with the shared bridge secret), checks the live impersonation
    session, then mints a normal tenant impersonation JWT and sets the same
    HttpOnly cookie used by a login — so the admin panel just works.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    handshake = (body or {}).get("hs", "")
    if not handshake:
        raise HTTPException(status_code=400, detail="Missing handshake")
    payload = decode_impersonation_handshake(handshake)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired handshake")

    jti = payload["jti"]
    user_email = payload["sub"]
    org_id = payload["org_id"]
    impersonated_by = payload.get("imp_by")

    # The impersonation session must be live AND this handshake ticket must
    # not have been claimed before. The UPDATE acts as an atomic single-use
    # claim: only the first enter for a given handshake jti succeeds, so a
    # copied/replayed handshake link stops working immediately after use.
    row = (
        await db.execute(
            text(
                """
                UPDATE impersonation_sessions
                SET handshake_used_at = now()
                WHERE token_jti = :jti
                  AND handshake_used_at IS NULL
                  AND revoked_at IS NULL
                  AND expires_at > now()
                RETURNING org_id, expires_at
                """
            ),
            {"jti": jti},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Handshake already used or impersonation session is no longer active",
        )

    token = create_impersonation_token(user_email, str(org_id), jti)
    response = JSONResponse(
        content={
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "email": user_email,
                "org_id": str(org_id),
            },
            "impersonation": {
                "active": True,
                "user_email": user_email,
                "org_id": str(org_id),
                "impersonated_by": impersonated_by,
            },
        }
    )
    return _set_auth_cookie(response, token)


@router.get("/impersonate/status")
async def impersonate_status(
    request: Request,
    authorization: Optional[str] = Header(None),
    ds_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Report whether the current tenant session is an impersonation one.

    The admin panel shows a banner when active and blocks sensitive actions.
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif ds_token:
        token = ds_token
    if not token:
        return {"active": False}

    payload = decode_access_token(token)
    if not payload or not payload.get("imp"):
        return {"active": False}

    jti = payload.get("jti", "")
    row = (
        await db.execute(
            text(
                """
                SELECT user_email, org_id, expires_at
                FROM impersonation_sessions
                WHERE token_jti = :jti
                  AND revoked_at IS NULL
                  AND expires_at > now()
                """
            ),
            {"jti": jti},
        )
    ).mappings().first()
    if not row:
        return {"active": False}

    return {
        "active": True,
        "user_email": row["user_email"],
        "org_id": str(row["org_id"]),
        "expires_at": row["expires_at"].isoformat(),
    }


@router.post("/impersonate/exit")
async def impersonate_exit(
    request: Request,
    authorization: Optional[str] = Header(None),
    ds_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the active impersonation session and clear the tenant cookie."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif ds_token:
        token = ds_token

    exited_jti: Optional[str] = None
    exited_user_email: Optional[str] = None
    if token:
        payload = decode_access_token(token)
        if payload and payload.get("imp"):
            jti = payload.get("jti", "")
            if jti:
                row = (
                    await db.execute(
                        text(
                            """
                            UPDATE impersonation_sessions
                            SET revoked_at = now()
                            WHERE token_jti = :jti AND revoked_at IS NULL
                            RETURNING user_email
                            """
                        ),
                        {"jti": jti},
                    )
                ).mappings().first()
                if row:
                    exited_jti = jti
                    exited_user_email = row["user_email"]
                await db.commit()

    # Best-effort mirror to the control plane so super_admin.impersonations
    # does not stay 'active' until the admin clicks "End now" (F5). A control
    # plane outage must never block the tenant-side exit.
    if exited_jti:
        try:
            async with httpx.AsyncClient(
                timeout=5, base_url=settings.SUPER_ADMIN_API_URL
            ) as client:
                await client.post(
                    "/api/v1/impersonation/tenant-exit",
                    headers=_support_service_headers(),
                    json={
                        "token_jti": exited_jti,
                        "user_email": exited_user_email,
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("Impersonation exit mirror failed: %s", exc)

    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="ds_token", path="/")
    return response
