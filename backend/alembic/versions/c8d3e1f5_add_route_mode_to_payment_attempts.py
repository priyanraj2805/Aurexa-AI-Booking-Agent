"""add route_mode to payment_attempts

Revision ID: c8d3e1f5
Revises: b7c2d9e4
Create Date: 2026-09-09

Additive nullable marker distinguishing legacy sub-merchant attempts
(NULL/LEGACY) from platform Route attempts (ROUTE). Existing rows keep NULL
and behave exactly as before. Required so the settlement orchestrator never
mistakes a legacy TEST-fallback platform order for a Route payment.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8d3e1f5"
down_revision: Union[str, Sequence[str], None] = "b7c2d9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("route_mode", sa.String(length=10), nullable=True))
    op.create_index("ix_payment_attempt_route", "payment_attempts", ["route_mode"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payment_attempt_route", table_name="payment_attempts")
    op.drop_column("payment_attempts", "route_mode")
