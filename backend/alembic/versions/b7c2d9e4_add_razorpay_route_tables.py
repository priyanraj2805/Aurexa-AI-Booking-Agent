"""add razorpay route tables (linked accounts + transfers)

Revision ID: b7c2d9e4
Revises: f6a7b8c9
Create Date: 2026-09-09

Additive only: legacy sub-merchant columns on org_configs are untouched.
Reuses razorpay_webhook_events for idempotency (no new event table).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b7c2d9e4"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "razorpay_linked_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_mode", sa.String(length=10), server_default="AUTO", nullable=False),
        sa.Column("razorpay_account_id", sa.String(length=32), nullable=True),
        sa.Column("razorpay_reference_id", sa.String(length=64), nullable=False),
        sa.Column("razorpay_status", sa.String(length=32), nullable=True),
        sa.Column("route_product_id", sa.String(length=32), nullable=True),
        sa.Column("route_product_status", sa.String(length=32), nullable=True),
        sa.Column("stakeholder_id", sa.String(length=32), nullable=True),
        sa.Column("onboarding_status", sa.String(length=32), server_default="NOT_STARTED", nullable=False),
        sa.Column("bank_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("bank_last4", sa.String(length=4), nullable=True),
        sa.Column("beneficiary_name", sa.String(length=255), nullable=True),
        sa.Column("transfer_ready_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tnc_accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("commission_bps", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_route_linked_org"),
        sa.UniqueConstraint("razorpay_account_id", name="uq_route_linked_account"),
    )
    op.create_index("ix_route_linked_org", "razorpay_linked_accounts", ["org_id"], unique=False)
    op.create_index("ix_route_linked_onboarding", "razorpay_linked_accounts", ["onboarding_status"], unique=False)
    op.create_index("ix_route_linked_product", "razorpay_linked_accounts", ["route_product_status"], unique=False)

    op.create_table(
        "razorpay_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=False),
        sa.Column("razorpay_account_id", sa.String(length=32), nullable=False),
        sa.Column("razorpay_transfer_id", sa.String(length=32), nullable=True),
        sa.Column("gross_paise", sa.Integer(), nullable=False),
        sa.Column("commission_paise", sa.Integer(), nullable=False),
        sa.Column("provider_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="INR", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("razorpay_reversal_id", sa.String(length=32), nullable=True),
        sa.Column("razorpay_refund_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reversed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_attempt_id"], ["payment_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_route_transfer_payment"),
        sa.UniqueConstraint("idempotency_key", name="uq_route_transfer_idem"),
        sa.UniqueConstraint("razorpay_transfer_id", name="uq_route_transfer_trf"),
    )
    op.create_index("ix_razorpay_transfers_org_id", "razorpay_transfers", ["org_id"], unique=False)
    op.create_index("ix_razorpay_transfers_order", "razorpay_transfers", ["razorpay_order_id"], unique=False)
    op.create_index("ix_route_transfer_org_status", "razorpay_transfers", ["org_id", "status"], unique=False)
    op.create_index("ix_route_transfer_booking", "razorpay_transfers", ["booking_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_route_transfer_booking", table_name="razorpay_transfers")
    op.drop_index("ix_route_transfer_org_status", table_name="razorpay_transfers")
    op.drop_index("ix_razorpay_transfers_order", table_name="razorpay_transfers")
    op.drop_index("ix_razorpay_transfers_org_id", table_name="razorpay_transfers")
    op.drop_table("razorpay_transfers")
    op.drop_index("ix_route_linked_product", table_name="razorpay_linked_accounts")
    op.drop_index("ix_route_linked_onboarding", table_name="razorpay_linked_accounts")
    op.drop_index("ix_route_linked_org", table_name="razorpay_linked_accounts")
    op.drop_table("razorpay_linked_accounts")
