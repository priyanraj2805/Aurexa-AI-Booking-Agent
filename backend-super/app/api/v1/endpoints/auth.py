from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import pyotp

from app.core.audit import write_audit_log
from app.core.config import settings
from app.core.limiter import limiter
from app.core.rbac import CurrentAdmin, get_current_admin
from app.core.security import (
    PURPOSE_MFA_PENDING,
    PURPOSE_REFRESH,
    create_access_token,
    create_mfa_pending_token,
    create_refresh_token,
    create_stepup_token,
    decode_token,
    generate_token_hash,
    hash_password,
    random_jti,
    verify_password,
)
from app.core.security_policy import get_security_settings, validate_password_policy
from app.db.database import get_db
from app.models.super_admin import AdminMFA, AdminSession, LoginAttempt, SuperAdmin
from app.schemas.auth import (
    AdminProfile,
    LoginRequest,
    LoginResponse,
    MFAEnrollResponse,
    MFAVerifyRequest,
)

router = APIRouter()

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15

ACCESS_COOKIE = settings.SUPER_ADMIN_COOKIE_NAME
REFRESH_COOKIE = settings.SUPER_ADMIN_REFRESH_COOKIE_NAME
MFA_PENDING_COOKIE = "super_admin_mfa_pending"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, key: str, value: str, max_age: int) -> None:
    if settings.DEBUG:
        # Local dev over http://localhost — secure cookies wouldn't be sent.
        response.set_cookie(
            key=key,
            value=value,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=max_age,
            path="/",
        )
    else:
        # Production: SameSite=None requires Secure (Cloudflare tunnel support)
        response.set_cookie(
            key=key,
            value=value,
            httponly=True,
            secure=True,
            samesite="none",
            max_age=max_age,
            path="/",
        )


def _clear_cookie(response: Response, key: str) -> None:
    response.delete_cookie(key=key, path="/")


async def _is_locked_out(db: AsyncSession, email: str, ip: str) -> bool:
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    result = await db.execute(
        select(LoginAttempt).where(
            LoginAttempt.email == email,
            LoginAttempt.attempted_at >= since,
            LoginAttempt.success.is_(False),
        )
    )
    attempts = result.scalars().all()
    ip_blocked = sum(1 for a in attempts if a.ip_address == ip)
    return len(attempts) >= LOCKOUT_MAX_ATTEMPTS or ip_blocked >= LOCKOUT_MAX_ATTEMPTS


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = payload.email.lower().strip()
    ip = _client_ip(request)

    if await _is_locked_out(db, email, ip):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
        )

    result = await db.execute(select(SuperAdmin).where(SuperAdmin.email == email))
    admin = result.scalar_one_or_none()

    if not admin:
        # Constant-time-ish dummy verify to avoid user-enumeration timing
        verify_password(payload.password, hash_password("dummy-password"))
        db.add(LoginAttempt(email=email, ip_address=ip, success=False))
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(payload.password, admin.password_hash):
        db.add(LoginAttempt(email=email, admin_id=admin.id, ip_address=ip, success=False))
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if admin.status != "active":
        raise HTTPException(status_code=403, detail="Admin account is disabled")

    # Success resets the lockout counter
    await db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    db.add(LoginAttempt(email=email, admin_id=admin.id, ip_address=ip, success=True))
    await db.commit()

    # Password step OK → hand over to MFA step with a short-lived token
    mfa_result = await db.execute(
        select(AdminMFA).where(AdminMFA.admin_id == admin.id)
    )
    mfa = mfa_result.scalar_one_or_none()
    enrolled = bool(mfa and mfa.is_verified)

    pending = create_mfa_pending_token(admin.id, admin.email)
    _set_cookie(
        response, MFA_PENDING_COOKIE, pending, max_age=5 * 60
    )

    return LoginResponse(
        mfa={"required": admin.mfa_required, "enrolled": enrolled}
    )


@router.post("/mfa/enroll", response_model=MFAEnrollResponse)
async def mfa_enroll(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    mfa_pending: str = Cookie(None, alias=MFA_PENDING_COOKIE),
):
    payload = decode_token(mfa_pending or "", expected_purpose=PURPOSE_MFA_PENDING)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="MFA pending session expired")

    admin_id = payload["sub"]
    existing = (
        await db.execute(select(AdminMFA).where(AdminMFA.admin_id == admin_id))
    ).scalar_one_or_none()
    if existing and existing.is_verified:
        raise HTTPException(status_code=400, detail="MFA already enrolled")

    # Reuse the pending secret so a page reload (which re-runs enroll) does
    # not invalidate the QR the user already scanned.
    secret = existing.totp_secret if existing and not existing.is_verified else pyotp.random_base32()
    otpauth_url = pyotp.totp.TOTP(secret).provisioning_uri(
        name=payload.get("email", "admin"),
        issuer_name="DotStark Super Admin",
    )

    if existing:
        existing.totp_secret = secret
        existing.is_verified = False
    else:
        db.add(AdminMFA(admin_id=admin_id, totp_secret=secret, is_verified=False))
    await db.commit()

    import base64
    import io

    import qrcode
    from qrcode.image.svg import SvgPathImage

    img = qrcode.make(otpauth_url, image_factory=SvgPathImage, box_size=8)
    buf = io.BytesIO()
    img.save(buf)
    qr_svg = (
        "data:image/svg+xml;base64,"
        + base64.b64encode(buf.getvalue()).decode("utf-8")
    )

    return MFAEnrollResponse(secret=secret, otpauth_url=otpauth_url, qr_svg=qr_svg)


@router.post("/mfa/verify")
async def mfa_verify(
    payload: MFAVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    mfa_pending: str = Cookie(None, alias=MFA_PENDING_COOKIE),
):
    token = decode_token(mfa_pending or "", expected_purpose=PURPOSE_MFA_PENDING)
    if not token or "sub" not in token:
        raise HTTPException(status_code=401, detail="MFA pending session expired")

    admin_id = token["sub"]
    admin = (
        await db.execute(select(SuperAdmin).where(SuperAdmin.id == admin_id))
    ).scalar_one_or_none()
    if not admin or admin.status != "active":
        raise HTTPException(status_code=403, detail="Admin account unavailable")

    mfa = (
        await db.execute(select(AdminMFA).where(AdminMFA.admin_id == admin.id))
    ).scalar_one_or_none()
    if not mfa or not mfa.totp_secret:
        raise HTTPException(status_code=400, detail="MFA not enrolled")

    if not pyotp.TOTP(mfa.totp_secret).verify(payload.code, valid_window=2):
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    if not mfa.is_verified:
        mfa.is_verified = True
        mfa.verified_at = datetime.now(timezone.utc)

    # Single active session policy: revoke all other refresh sessions
    await db.execute(
        delete(AdminSession).where(
            AdminSession.admin_id == admin.id,
            AdminSession.revoked_at.is_(None),
        )
    )

    jti = random_jti()
    refresh_token = create_refresh_token(admin.id, jti)
    db.add(
        AdminSession(
            admin_id=admin.id,
            refresh_token_hash=generate_token_hash(refresh_token),
            jti=jti,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500],
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    admin.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    access_token = create_access_token(admin.id)
    _set_cookie(
        response,
        ACCESS_COOKIE,
        access_token,
        max_age=settings.SUPER_ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    _set_cookie(
        response,
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
    )
    _clear_cookie(response, MFA_PENDING_COOKIE)

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="admin.login",
        resource_type="super_admin",
        resource_id=str(admin.id),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500],
    )

    return {"status": "ok"}


@router.post("/mfa/stepup")
async def mfa_stepup(
    payload: MFAVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(get_current_admin),
):
    """Re-verify MFA for a sensitive action (Phase 5 refund safeguards).

    Issues a short-lived step-up cookie proving the admin re-entered a valid
    TOTP code; refund endpoints require it. Audit-logged.
    """
    mfa = (
        await db.execute(select(AdminMFA).where(AdminMFA.admin_id == admin.id))
    ).scalar_one_or_none()
    if not mfa or not mfa.totp_secret or not mfa.is_verified:
        raise HTTPException(status_code=403, detail="MFA not enrolled")

    if not pyotp.TOTP(mfa.totp_secret).verify(payload.code, valid_window=2):
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    stepup = create_stepup_token(admin.id)
    _set_cookie(
        response,
        settings.SUPER_ADMIN_STEPUP_COOKIE_NAME,
        stepup,
        max_age=settings.STEPUP_TOKEN_EXPIRE_MINUTES * 60,
    )
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="auth.stepup",
        resource_type="super_admin",
        resource_id=str(admin.id),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500],
    )
    return {"ok": True, "expires_in_minutes": settings.STEPUP_TOKEN_EXPIRE_MINUTES}


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    super_admin_refresh: str = Cookie(None, alias=REFRESH_COOKIE),
):
    token = decode_token(super_admin_refresh or "", expected_purpose=PURPOSE_REFRESH)
    if not token or "jti" not in token or "sub" not in token:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    jti = token["jti"]
    session = (
        await db.execute(
            select(AdminSession).where(AdminSession.refresh_token_hash == generate_token_hash(super_admin_refresh))
        )
    ).scalar_one_or_none()
    if not session or session.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    if session.jti != jti:
        raise HTTPException(status_code=401, detail="Refresh token mismatch")

    admin = (
        await db.execute(select(SuperAdmin).where(SuperAdmin.id == token["sub"]))
    ).scalar_one_or_none()
    if not admin or admin.status != "active":
        raise HTTPException(status_code=403, detail="Admin account unavailable")

    # Rotate: revoke old refresh, issue new one
    session.revoked_at = datetime.now(timezone.utc)
    new_jti = random_jti()
    new_refresh = create_refresh_token(admin.id, new_jti)
    db.add(
        AdminSession(
            admin_id=admin.id,
            refresh_token_hash=generate_token_hash(new_refresh),
            jti=new_jti,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500],
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    await db.commit()

    access_token = create_access_token(admin.id)
    _set_cookie(
        response,
        ACCESS_COOKIE,
        access_token,
        max_age=settings.SUPER_ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    _set_cookie(
        response,
        REFRESH_COOKIE,
        new_refresh,
        max_age=settings.SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    super_admin_refresh: str = Cookie(None, alias=REFRESH_COOKIE),
):
    if super_admin_refresh:
        session = (
            await db.execute(
                select(AdminSession).where(
                    AdminSession.refresh_token_hash
                    == generate_token_hash(super_admin_refresh)
                )
            )
        ).scalar_one_or_none()
        if session:
            from datetime import datetime, timezone

            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()

    _clear_cookie(response, ACCESS_COOKIE)
    _clear_cookie(response, REFRESH_COOKIE)
    _clear_cookie(response, MFA_PENDING_COOKIE)
    return {"status": "ok"}


@router.get("/me", response_model=AdminProfile)
async def me(current: CurrentAdmin = Depends(get_current_admin)):
    return AdminProfile(
        id=current.id,
        email=current.email,
        full_name=current.full_name,
        role_code=current.role_code,
        role_name=current.role_name,
        permissions=current.permissions,
    )


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: CurrentAdmin = Depends(get_current_admin),
):
    """Change the current admin's password, enforcing the platform password
    policy from super_admin.security_settings."""
    admin = (
        await db.execute(select(SuperAdmin).where(SuperAdmin.id == current.id))
    ).scalar_one()
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    settings_row = await get_security_settings(db)
    validate_password_policy(payload.new_password, settings_row["password_policy"])

    admin.password_hash = hash_password(payload.new_password)
    await db.commit()
    await write_audit_log(
        actor_email=admin.email,
        action="admin.password_change",
        resource_type="super_admin",
        resource_id=str(admin.id),
        ip_address=_client_ip(request),
    )
    return {"ok": True}
