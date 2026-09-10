"""Razorpay Route models (provider payouts).

Additive to the legacy sub-merchant columns on ``OrgConfig`` — those remain
untouched. One ``RazorpayLinkedAccount`` row per organization max
(``UNIQUE(org_id)``), one ``RazorpayTransfer`` row per captured payment.

Privacy: only ``bank_last4`` + ``beneficiary_name`` are stored. Raw account
numbers, IFSCs, PANs, and secrets are NEVER persisted here.
"""

import uuid
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class RouteOnboardingStatus:
    """Explicit onboarding state machine (no bare booleans)."""

    NOT_STARTED = "NOT_STARTED"
    ACCOUNT_CREATED = "ACCOUNT_CREATED"
    STAKEHOLDER_PENDING = "STAKEHOLDER_PENDING"
    PRODUCT_PENDING = "PRODUCT_PENDING"
    BANK_DETAILS_PENDING = "BANK_DETAILS_PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACTIVE = "ACTIVE"
    TRANSFER_READY = "TRANSFER_READY"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"

    ALL = frozenset(
        {
            NOT_STARTED,
            ACCOUNT_CREATED,
            STAKEHOLDER_PENDING,
            PRODUCT_PENDING,
            BANK_DETAILS_PENDING,
            UNDER_REVIEW,
            ACTIVE,
            TRANSFER_READY,
            SUSPENDED,
            FAILED,
        }
    )


class RouteProductStatus:
    """Mirror of Razorpay product ``activation_status`` values."""

    REQUESTED = "requested"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNDER_REVIEW = "under_review"
    ACTIVATED = "activated"
    SUSPENDED = "suspended"


class RouteTransferStatus:
    """Local transfer lifecycle (superset of Razorpay transfer statuses)."""

    PENDING = "pending"  # row persisted, not yet sent to Razorpay
    PROCESSING = "processing"  # sent (trf_*), awaiting webhook/final state
    PROCESSED = "processed"  # transfer.processed webhook
    FAILED = "failed"  # transfers.failed webhook or API error
    REVERSED = "reversed"
    PARTIALLY_REVERSED = "partially_reversed"


class PaymentRouteMode:
    """Per-org routing choice. AUTO = Route only when explicitly opted in AND ready."""

    AUTO = "AUTO"
    LEGACY = "LEGACY"
    ROUTE = "ROUTE"

    ALL = frozenset({AUTO, LEGACY, ROUTE})


class RazorpayLinkedAccount(Base):
    __tablename__ = "razorpay_linked_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    # Explicit routing choice for this org (AUTO default). No silent fallback:
    # ROUTE + not TRANSFER_READY => order rejected with provider_payout_not_ready.
    payment_mode = Column(String(10), nullable=False, default=PaymentRouteMode.AUTO, server_default="AUTO")

    # Primary external reference (acc_* from POST /v2/accounts). Null until created.
    razorpay_account_id = Column(String(32), nullable=True)
    # Stable Aurexa-side reference sent as Razorpay reference_id (org UUID based).
    razorpay_reference_id = Column(String(64), nullable=False)
    # Raw account status from Razorpay (created/suspended).
    razorpay_status = Column(String(32), nullable=True)

    route_product_id = Column(String(32), nullable=True)
    route_product_status = Column(String(32), nullable=True)
    stakeholder_id = Column(String(32), nullable=True)

    onboarding_status = Column(
        String(32), nullable=False, default=RouteOnboardingStatus.NOT_STARTED, server_default="NOT_STARTED"
    )

    # Safe metadata only — never raw account numbers / IFSC / PAN.
    bank_verified = Column(Boolean, nullable=False, default=False, server_default="false")
    bank_last4 = Column(String(4), nullable=True)
    beneficiary_name = Column(String(255), nullable=True)

    transfer_ready_at = Column(DateTime(timezone=True), nullable=True)
    tnc_accepted_at = Column(DateTime(timezone=True), nullable=True)

    # Per-org commission override in basis points (null = global default).
    commission_bps = Column(Integer, nullable=True)

    failure_reason = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        # One linked-account row per org max — prevents duplicate acc_* creation.
        UniqueConstraint("org_id", name="uq_route_linked_org"),
        UniqueConstraint("razorpay_account_id", name="uq_route_linked_account"),
        Index("ix_route_linked_org", "org_id"),
        Index("ix_route_linked_onboarding", "onboarding_status"),
        Index("ix_route_linked_product", "route_product_status"),
    )


class RazorpayTransfer(Base):
    __tablename__ = "razorpay_transfers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    payment_attempt_id = Column(UUID(as_uuid=True), ForeignKey("payment_attempts.id", ondelete="SET NULL"), nullable=True)

    razorpay_order_id = Column(String(64), nullable=True, index=True)
    razorpay_payment_id = Column(String(64), nullable=False)
    # Recipient at creation time (denormalized so history survives re-onboarding).
    razorpay_account_id = Column(String(32), nullable=False)
    razorpay_transfer_id = Column(String(32), nullable=True)

    # Money snapshot persisted BEFORE the Razorpay call (integer paise only).
    gross_paise = Column(Integer, nullable=False)
    commission_paise = Column(Integer, nullable=False)
    provider_paise = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="INR", server_default="INR")

    status = Column(String(32), nullable=False, default=RouteTransferStatus.PENDING, server_default="pending")

    idempotency_key = Column(String(64), nullable=False)

    failure_reason = Column(Text, nullable=True)

    # Refund/reversal tracking (populated by the refund path only).
    razorpay_reversal_id = Column(String(32), nullable=True)
    razorpay_refund_id = Column(String(32), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    reversed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # One transfer per captured payment — duplicate verify/webhook safe.
        UniqueConstraint("razorpay_payment_id", name="uq_route_transfer_payment"),
        UniqueConstraint("idempotency_key", name="uq_route_transfer_idem"),
        UniqueConstraint("razorpay_transfer_id", name="uq_route_transfer_trf"),
        Index("ix_route_transfer_org_status", "org_id", "status"),
        Index("ix_route_transfer_booking", "booking_id"),
    )
