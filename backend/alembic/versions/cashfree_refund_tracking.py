"""refund tracking on payment_attempts (auto-refund on cancellation).

Revision ID: cashfree_refund_tracking
Revises: cashfree_org_creds
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "cashfree_refund_tracking"
down_revision: Union[str, Sequence[str], None] = "cashfree_org_creds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("refund_id", sa.String(length=64), nullable=True))
    op.add_column("payment_attempts", sa.Column("refund_status", sa.String(length=20), nullable=True))
    op.add_column("payment_attempts", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_attempts", "refunded_at")
    op.drop_column("payment_attempts", "refund_status")
    op.drop_column("payment_attempts", "refund_id")
