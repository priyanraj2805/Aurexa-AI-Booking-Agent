"""
Security utilities — password hashing & JWT tokens.
"""
import os
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

# ── Password Hashing ──────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────
_jwt_secret = os.getenv("JWT_SECRET_KEY", "")
if not _jwt_secret:
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )
SECRET_KEY = _jwt_secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
IMPERSONATION_TOKEN_EXPIRE_MINUTES = 30  # Phase 8: impersonation tokens


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_impersonation_token(user_email: str, org_id: str, jti: str) -> str:
    """Short-lived token letting a super admin act as an org admin.

    Payload carries imp=true and a jti that get_current_user checks against
    public.impersonation_sessions, so revocation works before JWT expiry.
    """
    to_encode = {
        "sub": user_email,
        "org_id": org_id,
        "imp": True,
        "jti": jti,
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=IMPERSONATION_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_impersonation_handshake(handshake: str):
    """Validate a Phase-11b impersonation handshake ticket.

    The handshake is signed with the SHARED bridge secret (never the tenant
    JWT secret) by the super-admin control plane, so a leaked tenant JWT is
    never required to enter the impersonated session.
    """
    from app.core.config import settings

    try:
        payload = jwt.decode(
            handshake,
            settings.INTERNAL_BRIDGE_SECRET,
            algorithms=[ALGORITHM],
        )
    except JWTError:
        return None
    if payload.get("typ") != "imp_enter":
        return None
    return payload


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
