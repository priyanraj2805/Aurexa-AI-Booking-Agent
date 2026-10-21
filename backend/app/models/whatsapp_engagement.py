"""WhatsApp engagement models — opt-outs, leads, waitlist, delivery log.

One module for everything that only exists because the customer talks on
WhatsApp. Kept together so a reviewer can see the whole WhatsApp data
surface in one place instead of hunting across model files.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.db.base import Base


class WhatsAppOptOut(Base):
    """A customer who asked to stop receiving messages.

    Meta requires honouring STOP immediately — replying again risks the
    business number being banned. Checked on EVERY inbound WhatsApp
    message, before any other processing.
    """

    __tablename__ = "whatsapp_opt_outs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone = Column(String(32), nullable=False, index=True)
    channel = Column(String(20), nullable=False, default="whatsapp")
    reason = Column(String(64), nullable=True)  # stop | block | other
    source_session_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "phone", "channel", name="uq_wa_optout_org_phone"),
        Index("ix_wa_optout_org_created", "org_id", "created_at"),
    )


class BookingLead(Base):
    """A warm enquiry that is not (yet) a confirmed booking.

    Most WhatsApp conversations never reach a booking. Without this the
    demand is silently lost — the customer asked, the AI answered, and
    nobody in the business ever saw it.
    """

    __tablename__ = "booking_leads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    phone = Column(String(32), nullable=True, index=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    channel = Column(String(20), nullable=False, default="whatsapp")
    intent = Column(String(40), nullable=True)  # book | ask | price | complaint
    notes = Column(Text, nullable=True)
    context = Column(JSONB, nullable=True)  # collected fields snapshot
    status = Column(String(20), nullable=False, default="new")  # new | contacted | converted | closed
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_booking_lead_org_status", "org_id", "status"),
        Index("ix_booking_lead_org_created", "org_id", "created_at"),
    )


class BookingWaitlist(Base):
    """Customer waiting for a slot to free up.

    When the requested day has no availability the customer can wait; the
    next cancellation auto-books them and notifies them.
    """

    __tablename__ = "booking_waitlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    unit_id = Column(UUID(as_uuid=True), ForeignKey("service_units.id", ondelete="CASCADE"), nullable=True)
    provider_id = Column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="CASCADE"), nullable=True)
    phone = Column(String(32), nullable=True, index=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    target_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="waiting")  # waiting | notified | booked | expired
    notified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_booking_waitlist_org_status", "org_id", "status"),
        Index("ix_booking_waitlist_org_date", "org_id", "target_date"),
    )


class WhatsAppDeliveryLog(Base):
    """Per-message delivery log for WhatsApp sends (reminders, templates).

    Mirrors EmailDeliveryLog so the dedupe semantics are identical: a
    reminder is never sent twice for the same booking+channel.
    """

    __tablename__ = "whatsapp_delivery_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True)
    channel = Column(String(40), nullable=False)  # reminder_24h | reminder_1h | ...
    phone = Column(String(32), nullable=True, index=True)
    template_name = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="sent")  # sent | failed | skipped
    error = Column(Text, nullable=True)
    provider_message_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "booking_id", "channel", name="uq_wa_delivery_booking_channel"
        ),
        Index("ix_wa_delivery_org_created", "org_id", "created_at"),
    )


class BookingFunnelEvent(Base):
    """Per-step funnel events so the dashboard can show WHERE users drop.

    Web widget has the cards; WhatsApp is text-only, so the only way to
    see the drop-off point is to record every step transition.
    """

    __tablename__ = "booking_funnel_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    channel = Column(String(20), nullable=False, default="whatsapp")
    step = Column(String(40), nullable=False)  # started | dept | date | time | otp | payment | confirmed
    outcome = Column(String(20), nullable=False, default="entered")  # entered | completed | abandoned
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_booking_funnel_org_step", "org_id", "step"),
        Index("ix_booking_funnel_org_created", "org_id", "created_at"),
    )
