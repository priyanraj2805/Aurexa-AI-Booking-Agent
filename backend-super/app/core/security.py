"""
Security utilities — password hashing & JWT tokens for the Super Admin plane.

Uses a SEPARATE JWT secret (SUPER_ADMIN_JWT_SECRET_KEY) — never the tenant
backend's JWT_SECRET_KEY, so tokens from one system are rejected by the other.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# ── Password Hashing ──────────────────────────────


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT (separate secret from the tenant backend) ──

SECRET_KEY = settings.SUPER_ADMIN_JWT_SECRET_KEY
ALGORITHM = settings.SUPER_ADMIN_JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.SUPER_ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS

PURPOSE_ACCESS = "access"
PURPOSE_REFRESH = "refresh"
PURPOSE_MFA_PENDING = "mfa_pending"
PURPOSE_STEPUP = "stepup"


def _exp(expires_delta: timedelta) -> datetime:
    return datetime.now(timezone.utc) + expires_delta


def create_access_token(admin_id: UUID) -> str:
    to_encode = {
        "sub": str(admin_id),
        "purpose": PURPOSE_ACCESS,
        "exp": _exp(timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(admin_id: UUID, jti: str) -> str:
    to_encode = {
        "sub": str(admin_id),
        "purpose": PURPOSE_REFRESH,
        "jti": jti,
        "exp": _exp(timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_stepup_token(admin_id: UUID) -> str:
    """Short-lived token proving the admin re-verified MFA for a sensitive
    action (refunds, and later impersonation / kill switches)."""
    to_encode = {
        "sub": str(admin_id),
        "purpose": PURPOSE_STEPUP,
        "exp": _exp(timedelta(minutes=settings.STEPUP_TOKEN_EXPIRE_MINUTES)),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_mfa_pending_token(admin_id: UUID, email: str) -> str:
    """Short-lived (5 min) token proving the password step succeeded."""
    to_encode = {
        "sub": str(admin_id),
        "email": email,
        "purpose": PURPOSE_MFA_PENDING,
        "exp": _exp(timedelta(minutes=5)),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str, expected_purpose: Optional[str] = None) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if expected_purpose and payload.get("purpose") != expected_purpose:
        return None
    return payload


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_token_hash(token: str) -> str:
    return sha256_hex(token)


def random_jti() -> str:
    return secrets.token_urlsafe(32)
