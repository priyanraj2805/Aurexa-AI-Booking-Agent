"""add session archive and retention config

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "session_retention_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "session_cleanup_frequency",
            sa.String(length=20),
            nullable=False,
            server_default="daily",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "session_cleanup_hour_utc",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("org_configs", "session_cleanup_hour_utc")
    op.drop_column("org_configs", "session_cleanup_frequency")
    op.drop_column("org_configs", "session_retention_days")
    op.drop_column("chat_sessions", "archived_at")
