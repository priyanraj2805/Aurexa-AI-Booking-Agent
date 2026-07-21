from pydantic import BaseModel, EmailStr, UUID4, ConfigDict, Field
from datetime import datetime
from typing import Optional


# ── Request Schemas ───────────────────────────────

class UserRegister(BaseModel):
    """Registration: creates user + org in one step."""
    full_name: str = Field(min_length=2, max_length=255)
    org_name: str = Field(min_length=2, max_length=255)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    email: str
    password: str


# ── Response Schemas ──────────────────────────────

class UserResponse(BaseModel):
    id: UUID4
    email: str
    full_name: str
    org_id: UUID4
    org_name: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
