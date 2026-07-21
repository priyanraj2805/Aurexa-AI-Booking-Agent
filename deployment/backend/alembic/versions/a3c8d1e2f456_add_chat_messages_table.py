"""Add chat_messages table for transcripts

Revision ID: a3c8d1e2f456
Revises: fea6fd19bee6
Create Date: 2026-03-15 09:45:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a3c8d1e2f456"
down_revision: Union[str, None] = "fea6fd19bee6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_msg_session_created", "chat_messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_msg_session_created", table_name="chat_messages")
    op.drop_table("chat_messages")
