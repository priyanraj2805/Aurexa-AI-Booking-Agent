from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MFAStatus(BaseModel):
    required: bool
    enrolled: bool


class LoginResponse(BaseModel):
    mfa: MFAStatus


class MFAEnrollResponse(BaseModel):
    secret: str
    otpauth_url: str
    qr_svg: str


class MFAVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class AdminProfile(BaseModel):
    id: UUID
    email: str
    full_name: Optional[str]
    role_code: str
    role_name: str
    permissions: List[str]
