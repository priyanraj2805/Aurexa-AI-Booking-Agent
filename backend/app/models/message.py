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

    # Human handoff fields
    sender_type = Column(String(20), default="ai", nullable=False)  # "user" | "ai" | "human"
    human_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Inbound provider message id (wamid / Baileys key.id) for user rows.
    # Links a transcript row back to the exact provider message that produced
    # it, even when directive-tap handling rewrites the text downstream.
    external_message_id = Column(String(255), nullable=True, index=True)

    # Outbound delivery tracking — Generated, Sent, Delivered, Read, or Failed.
    # Transcript existence is not proof of provider delivery.
    channel = Column(String(20), nullable=True)  # whatsapp, whatsapp_cloud, web
    direction = Column(String(10), nullable=True)  # inbound / outbound
    send_status = Column(String(20), nullable=True)  # GENERATED, SENT, DELIVERED, READ, FAILED
    provider_message_id = Column(String(255), nullable=True)  # wamid from Baileys/Meta
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_msg_session_created", "session_id", "created_at"),
        Index("ix_msg_sender_type", "sender_type"),
        Index("ix_msg_send_status", "send_status"),
        Index("ix_msg_provider_id", "provider_message_id"),
        Index("ix_msg_channel", "channel"),
    )
