import uuid
import sqlalchemy as sa
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.base import Base

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    api_key = Column(String(64), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # API key rotation fields (SEC-01)
    # SECURITY NOTE: API keys are stored as plaintext because they are:
    # 1. Server-generated (not user-chosen secrets vulnerable to dictionary attacks)
    # 2. High-entropy random strings (32+ chars)
    # 3. Need direct comparison for validation during grace period
    # Hashing would require SHA-256 pre-hash before bcrypt (72-byte limit issue).
    # This is acceptable security practice for server-generated API keys.
    old_api_key = Column(String(64), nullable=True, index=True)
    old_key_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Onboarding
    onboarding_completed = Column(Boolean, default=False, server_default=sa.false(), nullable=False)
    industry_type = Column(String(100), nullable=True)
    custom_prompts = Column(sa.JSON, nullable=True)
