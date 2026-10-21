"""cashfree provider columns + webhook events table.

Revision ID: cashfree_provider
Revises: chat_message_external_id
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "cashfree_provider"
down_revision: Union[str, Sequence[str], None] = "chat_message_external_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("provider", sa.String(length=20), nullable=False, server_default="razorpay"))
    op.add_column("payment_attempts", sa.Column("provider_order_id", sa.String(length=64), nullable=True))
    op.add_column("payment_attempts", sa.Column("provider_payment_id", sa.String(length=64), nullable=True))
    op.alter_column("payment_attempts", "razorpay_order_id", existing_type=sa.String(length=64), nullable=True)
    # Backfill legacy rows, then enforce uniqueness for the new lookup key.
    op.execute("UPDATE payment_attempts SET provider = 'razorpay' WHERE provider IS NULL")
    op.execute("UPDATE payment_attempts SET provider_order_id = razorpay_order_id WHERE provider_order_id IS NULL AND razorpay_order_id IS NOT NULL")
    op.execute("UPDATE payment_attempts SET provider_payment_id = razorpay_payment_id WHERE provider_payment_id IS NULL AND razorpay_payment_id IS NOT NULL")
    op.create_index("ix_attempt_provider_order", "payment_attempts", ["provider_order_id"], unique=True)
    op.create_index("ix_attempt_provider_payment", "payment_attempts", ["provider_payment_id"])
    op.create_table(
        "cashfree_webhook_events",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("org_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="processed"),
    )


def downgrade() -> None:
    op.drop_table("cashfree_webhook_events")
    op.drop_index("ix_attempt_provider_payment", table_name="payment_attempts")
    op.drop_index("ix_attempt_provider_order", table_name="payment_attempts")
    op.drop_column("payment_attempts", "provider_payment_id")
    op.drop_column("payment_attempts", "provider_order_id")
    op.drop_column("payment_attempts", "provider")
    op.alter_column("payment_attempts", "razorpay_order_id", existing_type=sa.String(length=64), nullable=False)
