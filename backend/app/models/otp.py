import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.base import Base

class OTPRecord(Base):
    __tablename__ = "otp_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    channel = Column(String(10), nullable=False, server_default="email", default="email")
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    failed_attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("session_id", "channel", name="uq_otp_session_channel"),)


class AuthOTPRecord(Base):
    """Email OTP for admin-panel authentication (register/login verification).

    Unlike OTPRecord (booking-flow OTP tied to a chat session), this record
    is tied to the admin's email address and a purpose so the same code is
    never shared across registration and login flows.
    """

    __tablename__ = "auth_otp_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    purpose = Column(String(20), nullable=False)  # "register" | "login"
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    failed_attempts = Column(Integer, default=0, nullable=False)
    resend_count = Column(Integer, default=0, nullable=False)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("email", "purpose", name="uq_auth_otp_email_purpose"),)
