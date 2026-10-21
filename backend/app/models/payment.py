import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index, UniqueConstraint, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.db.base import Base


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    # Nullable until paid: booking is created only after verified payment
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)

    razorpay_order_id = Column(String(64), nullable=True, unique=True, index=True)
    razorpay_payment_id = Column(String(64), nullable=True, index=True)
    razorpay_signature = Column(String(255), nullable=True)

    # Provider-agnostic collection (razorpay | cashfree). Legacy rows carry
    # provider='razorpay' with provider_order_id == razorpay_order_id.
    provider = Column(String(20), nullable=False, default="razorpay", server_default="razorpay")
    provider_order_id = Column(String(64), nullable=True, unique=True, index=True)
    provider_payment_id = Column(String(64), nullable=True, index=True)

    amount_paise = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="INR")

    status = Column(String(20), nullable=False, default="created")  # created | paid | failed | expired
    verification_status = Column(String(20), nullable=False, default="pending")  # pending | verified | failed

    # Route payout path marker. NULL/LEGACY = legacy sub-merchant flow (unchanged
    # behavior); ROUTE = platform order feeding a RazorpayTransfer. Additive only.
    route_mode = Column(String(10), nullable=True)

    # For slot hold extension
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Raw webhook payload for audit
    webhook_payload = Column(JSONB, nullable=True)

    # Refund tracking (auto-refund on cancellation, 1-day policy).
    refund_id = Column(String(64), nullable=True)
    refund_status = Column(String(20), nullable=True)  # initiated | succeeded | failed
    refunded_at = Column(DateTime(timezone=True), nullable=True)

    # Shareable checkout URL (Cashfree payment link) for channels without a
    # JS checkout (WhatsApp). Lets re-surface reuse the same link instead of
    # minting a new order every turn.
    checkout_url = Column(String(512), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_payment_attempt_org_created", "org_id", "created_at"),
        Index("ix_payment_attempt_session", "session_id", "status"),
        Index("ix_payment_attempt_expires", "expires_at", "status"),
    )


class RazorpayWebhookEvent(Base):
    __tablename__ = "razorpay_webhook_events"

    event_id = Column(String(128), primary_key=True)  # Razorpay event id for idempotency
    org_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status = Column(String(20), nullable=False, default="processed")  # processed | ignored | failed


class CashfreeWebhookEvent(Base):
    __tablename__ = "cashfree_webhook_events"

    event_id = Column(String(128), primary_key=True)  # cf_payment_id or order_id fallback, for idempotency
    org_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status = Column(String(20), nullable=False, default="processed")  # processed | ignored | failed
