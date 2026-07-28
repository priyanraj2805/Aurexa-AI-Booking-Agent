import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Boolean, Index, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.db.base import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = Column(String(20), default="web", nullable=False)
    channel_user_id = Column(String(255), nullable=True)
    state_data = Column(JSONB, default=dict)
    missing_fields = Column(JSONB, default=list)
    current_intent = Column(String(50), nullable=True)
    session_status = Column(String(20), default="ACTIVE", nullable=False)
    otp_verified = Column(Boolean, default=False, nullable=False)
    pending_cancel = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Human handoff fields
    human_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    human_agent_active = Column(Boolean, default=False, nullable=False)
    ai_paused_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_session_org_status", "org_id", "session_status"),
        Index("ix_session_org_updated", "org_id", "updated_at"),
        Index("ix_session_channel_user", "org_id", "channel", "channel_user_id"),
    )
