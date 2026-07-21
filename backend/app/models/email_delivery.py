import uuid
from sqlalchemy import Column, String, DateTime, Integer, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.db.base import Base


class EmailDeliveryLog(Base):
    __tablename__ = "email_delivery_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    channel = Column(String(40), nullable=False)  # otp | booking_confirmation | booking_cancellation
    recipient_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    provider = Column(String(32), nullable=False, default="primary")
    status = Column(String(20), nullable=False, default="pending")  # pending|sent|failed|bounced
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(2000), nullable=True)
    request_id = Column(String(128), nullable=True)
    payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_email_delivery_org_created", "org_id", "created_at"),
        Index("ix_email_delivery_org_status_created", "org_id", "status", "created_at"),
        Index("ix_email_delivery_retry_scan", "next_retry_at", "status"),
    )

