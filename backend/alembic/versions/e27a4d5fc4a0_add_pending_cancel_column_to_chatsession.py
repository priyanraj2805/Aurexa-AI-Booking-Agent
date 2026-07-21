"""Add pending_cancel column to ChatSession

Revision ID: e27a4d5fc4a0
Revises: f96fa515c57d
Create Date: 2026-03-25 17:02:55.113859

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e27a4d5fc4a0"
down_revision: Union[str, Sequence[str], None] = "f96fa515c57d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Check if column exists, if not add it
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='chat_sessions' AND column_name='pending_cancel'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "chat_sessions",
            sa.Column(
                "pending_cancel", sa.Boolean(), nullable=True, server_default="false"
            ),
        )

    # Update any null values to false
    op.execute(
        sa.text(
            "UPDATE chat_sessions SET pending_cancel = false WHERE pending_cancel IS NULL"
        )
    )

    # Now alter to not nullable
    op.alter_column("chat_sessions", "pending_cancel", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("chat_sessions", "pending_cancel")
