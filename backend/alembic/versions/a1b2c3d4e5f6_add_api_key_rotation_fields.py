"""add_api_key_rotation_fields

Revision ID: a1b2c3d4e5f6
Revises: fea6fd19bee6
Create Date: 2026-03-29

SEC-01: API key rotation with grace period support.
Adds fields to track old API key during rotation transition.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str = 'fea6fd19bee6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add old_api_key column (String(64), nullable, indexed)
    # Used during rotation to maintain validity of previous key
    op.add_column(
        'organizations',
        sa.Column('old_api_key', sa.String(64), nullable=True)
    )
    op.create_index(
        'ix_organizations_old_api_key',
        'organizations',
        ['old_api_key'],
        unique=False
    )

    # Add old_key_expires_at column (DateTime with timezone, nullable)
    # Defines when the old key stops being valid (grace period end)
    op.add_column(
        'organizations',
        sa.Column('old_key_expires_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_index('ix_organizations_old_api_key', table_name='organizations')
    op.drop_column('organizations', 'old_key_expires_at')
    op.drop_column('organizations', 'old_api_key')
