"""add logo_url to org_configs

Revision ID: a1b2c3d4
Revises: 9f2b8c1d
Create Date: 2026-08-27
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "9f2b8c1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("org_configs", sa.Column("logo_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("org_configs", "logo_url")
