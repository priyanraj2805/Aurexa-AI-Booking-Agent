"""per-org cashfree credentials on org_configs.

Revision ID: cashfree_org_creds
Revises: cashfree_provider
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "cashfree_org_creds"
down_revision: Union[str, Sequence[str], None] = "cashfree_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("org_configs", sa.Column("payment_provider", sa.String(length=20), nullable=False, server_default="razorpay"))
    op.add_column("org_configs", sa.Column("cashfree_app_id_enc", sa.Text(), nullable=True))
    op.add_column("org_configs", sa.Column("cashfree_secret_enc", sa.Text(), nullable=True))
    op.add_column("org_configs", sa.Column("cashfree_mode", sa.String(length=10), nullable=False, server_default="test"))


def downgrade() -> None:
    op.drop_column("org_configs", "cashfree_mode")
    op.drop_column("org_configs", "cashfree_secret_enc")
    op.drop_column("org_configs", "cashfree_app_id_enc")
    op.drop_column("org_configs", "payment_provider")
