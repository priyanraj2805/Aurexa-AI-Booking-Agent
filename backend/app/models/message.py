import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.db.base import Base


class ChatMessage(Base):
    """Individual message in a chat session transcript."""
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(10), nullable=False)  # "user" or "agent"
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=True)  # directive, field_meta, intent, etc.
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_msg_session_created", "session_id", "created_at"),
    )
