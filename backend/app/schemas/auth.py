from pydantic import BaseModel, EmailStr, UUID4, ConfigDict, Field
from datetime import datetime
from typing import List, Literal, Optional


# ── Request Schemas ───────────────────────────────

class UserRegister(BaseModel):
    """Registration: creates user + org in one step, gated by email OTP."""
    full_name: str = Field(min_length=2, max_length=255)
    org_name: str = Field(min_length=2, max_length=255)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    otp_code: str = Field(min_length=6, max_length=6)


class UserLogin(BaseModel):
    email: str
    password: str


class PasswordForgotRequest(BaseModel):
    """Request a password-reset OTP be emailed to an existing account."""
    email: str


class PasswordResetRequest(BaseModel):
    """Complete a password reset with the emailed OTP + a new password."""
    email: str
    otp_code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6, max_length=128)


class OTPSendRequest(BaseModel):
    """Request an OTP be emailed to verify a new account registration."""
    email: str
    purpose: Literal["register"] = "register"


# ── Response Schemas ──────────────────────────────

class UserResponse(BaseModel):
    id: UUID4
    email: str
    full_name: str
    org_id: UUID4
    org_name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    # RBAC: role codes + effective permissions (authoritative per request).
    roles: List[str] = []
    permissions: List[str] = []

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
