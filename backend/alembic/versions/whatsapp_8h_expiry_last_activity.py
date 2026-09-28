"""whatsapp 8h inactivity expiry - add last_activity_at

Revision ID: whatsapp_8h_expiry
Revises: rbac_002
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "whatsapp_8h_expiry"
down_revision: Union[str, Sequence[str], None] = "rbac_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_session_last_activity",
        "chat_sessions",
        ["last_activity_at"],
    )
    # Backfill for existing rows
    op.execute(
        "UPDATE chat_sessions SET last_activity_at = COALESCE(updated_at, created_at) WHERE last_activity_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_session_last_activity", table_name="chat_sessions")
    op.drop_column("chat_sessions", "last_activity_at")
