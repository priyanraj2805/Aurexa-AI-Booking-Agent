import logging
import traceback
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import Header, Cookie
from typing import Optional
from app.db.database import get_db
from app.crud.user import get_user_by_email, authenticate_user, create_user_with_org
from app.schemas.auth import UserRegister, UserLogin, UserResponse, TokenResponse
from app.core.security import create_access_token, decode_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from app.models.user import User
from app.models.organization import Organization
from app.core.config import settings
from app.core.limiter import limiter
from app.core.audit import log_audit_event
from app.schemas.audit import AuditAction

router = APIRouter()
logger = logging.getLogger(__name__)


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
    result = await db.execute(select(User).where(User.email == user_email))
    user = result.scalars().first()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


# ── Endpoints ─────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
@limiter.limit("5/minute")
async def register(
    request: Request,
    data: UserRegister,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account + organization."""
    try:
        existing = await get_user_by_email(db, data.email.lower().strip())
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists")

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
                is_active=user.is_active,
                created_at=user.created_at,
            ),
        )
        response = JSONResponse(content=body.model_dump(mode="json"))
        return _set_auth_cookie(response, token)
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
    """Authenticate and return JWT."""
    try:
        user = await authenticate_user(db, data.email.lower().strip(), data.password)
        if not user:
            result = await db.execute(select(User).where(User.email == data.email.lower().strip()))
            existing_user = result.scalars().first()
            if existing_user:
                log_audit_event(
                    background_tasks,
                    org_id=existing_user.org_id,
                    actor_id=existing_user.id,
                    actor_email=existing_user.email,
                    action=AuditAction.AUTH_LOGIN_FAILED,
                    resource_type="user",
                    resource_id=str(existing_user.id),
                    old_values={"event": "login_attempt"},
                    new_values={"result": "failed_invalid_credentials"},
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
            raise HTTPException(status_code=401, detail="Invalid email or password")

        # Fetch org name
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
