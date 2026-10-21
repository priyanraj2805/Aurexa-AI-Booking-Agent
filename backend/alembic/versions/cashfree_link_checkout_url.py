"""checkout_url on payment_attempts (WhatsApp payment links).

Revision ID: cashfree_link_checkout_url
Revises: cashfree_refund_tracking
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "cashfree_link_checkout_url"
down_revision: Union[str, Sequence[str], None] = "cashfree_refund_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("checkout_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_attempts", "checkout_url")
