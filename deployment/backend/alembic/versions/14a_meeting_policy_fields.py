"""add meeting policy fields to org config

Revision ID: 14a_meeting_policy_fields
Revises: e6f7a8b9c0d1
Create Date: 2026-03-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "14a_meeting_policy_fields"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "org_configs",
        sa.Column(
            "meeting_mode",
            sa.String(length=20),
            nullable=False,
            server_default="OFFLINE_ONLY",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "allow_hybrid_selection",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "meeting_provider_default",
            sa.String(length=32),
            nullable=False,
            server_default="NONE",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column("meeting_policy_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "org_configs",
        sa.Column("meeting_policy_updated_by", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_configs", "meeting_policy_updated_by")
    op.drop_column("org_configs", "meeting_policy_updated_at")
    op.drop_column("org_configs", "meeting_provider_default")
    op.drop_column("org_configs", "allow_hybrid_selection")
    op.drop_column("org_configs", "meeting_mode")
