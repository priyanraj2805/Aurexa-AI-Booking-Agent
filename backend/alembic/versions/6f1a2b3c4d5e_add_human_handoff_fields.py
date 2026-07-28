"""Add human handoff fields to conversations and messages

Revision ID: 6f1a2b3c4d5e
Revises: 15a_tz_sched_policy_fields
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6f1a2b3c4d5e'
down_revision: Union[str, Sequence[str], None] = '15a_tz_sched_policy_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.scalar() is not None


def _index_exists(bind, index: str) -> bool:
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": index},
    )
    return result.scalar() is not None


def _constraint_exists(bind, name: str) -> bool:
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
        {"n": name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    """Upgrade schema (idempotent: skips objects that already exist)."""
    bind = op.get_bind()

    # --- chat_sessions: handoff tracking columns ---
    if not _column_exists(bind, "chat_sessions", "status"):
        op.add_column(
            "chat_sessions",
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        )
    if not _column_exists(bind, "chat_sessions", "human_agent_id"):
        op.add_column(
            "chat_sessions",
            sa.Column("human_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if not _column_exists(bind, "chat_sessions", "human_agent_active"):
        op.add_column(
            "chat_sessions",
            sa.Column(
                "human_agent_active",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
        )
    if not _column_exists(bind, "chat_sessions", "ai_paused_at"):
        op.add_column(
            "chat_sessions",
            sa.Column("ai_paused_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _column_exists(bind, "chat_sessions", "handoff_requested_at"):
        op.add_column(
            "chat_sessions",
            sa.Column("handoff_requested_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _constraint_exists(bind, "fk_chat_sessions_human_agent_id"):
        op.create_foreign_key(
            "fk_chat_sessions_human_agent_id",
            "chat_sessions",
            "users",
            ["human_agent_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _index_exists(bind, "ix_chat_sessions_human_agent_active"):
        op.create_index(
            "ix_chat_sessions_human_agent_active", "chat_sessions", ["human_agent_active"]
        )
    if not _index_exists(bind, "ix_chat_sessions_status"):
        op.create_index("ix_chat_sessions_status", "chat_sessions", ["status"])

    # --- chat_messages: sender type tracking ---
    if not _column_exists(bind, "chat_messages", "sender_type"):
        op.add_column(
            "chat_messages",
            sa.Column("sender_type", sa.String(20), nullable=False, server_default="ai"),
        )
    if not _column_exists(bind, "chat_messages", "human_agent_id"):
        op.add_column(
            "chat_messages",
            sa.Column("human_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        )

    if not _constraint_exists(bind, "fk_chat_messages_human_agent_id"):
        op.create_foreign_key(
            "fk_chat_messages_human_agent_id",
            "chat_messages",
            "users",
            ["human_agent_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _index_exists(bind, "ix_chat_messages_sender_type"):
        op.create_index(
            "ix_chat_messages_sender_type", "chat_messages", ["sender_type"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chat_messages_sender_type", table_name="chat_messages")
    op.drop_index("ix_chat_sessions_status", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_human_agent_active", table_name="chat_sessions")

    op.drop_constraint("fk_chat_messages_human_agent_id", "chat_messages", type_="foreignkey")
    op.drop_constraint("fk_chat_sessions_human_agent_id", "chat_sessions", type_="foreignkey")

    op.drop_column("chat_messages", "human_agent_id")
    op.drop_column("chat_messages", "sender_type")

    op.drop_column("chat_sessions", "handoff_requested_at")
    op.drop_column("chat_sessions", "ai_paused_at")
    op.drop_column("chat_sessions", "human_agent_active")
    op.drop_column("chat_sessions", "human_agent_id")
    op.drop_column("chat_sessions", "status")
