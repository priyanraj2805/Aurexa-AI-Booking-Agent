"""BookingDirective — structured booking actions (WhatsApp interactive).

One directive per user action (SELECT_DATE, SELECT_SLOT, etc.). Booking engine
creates it, WhatsApp renderer turns it into buttons/list, webhook handler
completes it. No LLM for structured taps. Idempotent via status + dedup.
"""

import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.base import Base


class DirectiveType(str):
    SELECT_DATE = "SELECT_DATE"
    SELECT_TIME = "SELECT_TIME"
    SELECT_SLOT = "SELECT_SLOT"
    CONFIRM_BOOKING = "CONFIRM_BOOKING"
    CHANGE_SELECTION = "CHANGE_SELECTION"
    CANCEL_BOOKING = "CANCEL_BOOKING"


class DirectiveStatus(str):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class BookingDirective(Base):
    __tablename__ = "booking_directives"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(String(64), nullable=False)  # channel_user_id (phone digits) or session_id
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)

    type = Column(String(32), nullable=False)
    status = Column(String(20), nullable=False, default=DirectiveStatus.PENDING, server_default="PENDING")

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    options = Column(JSONB, nullable=False, default=list)  # [{id,label,value}]
    required = Column(Boolean, nullable=False, default=True, server_default="true")

    expires_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    selected_option = Column(JSONB, nullable=True)

    meta = Column(JSONB, nullable=False, default=dict)  # {field_name, slot_date, ...}

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_directives_org", "org_id"),
        Index("ix_directives_session", "session_id"),
        Index("ix_directives_customer", "customer_id"),
        Index("ix_directives_status", "status"),
        Index("ix_directives_expires", "expires_at"),
    )
