"""add external_message_id to chat_messages

Stores the inbound provider message id (Meta wamid / Baileys key.id) on
user transcript rows, so a transcript row can always be traced back to the
exact provider message that produced it — even when directive-tap handling
rewrites the text before the engine persists it.

Revision ID: chat_message_external_id
Revises: reconcile_orm_schema
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "chat_message_external_id"
down_revision: Union[str, Sequence[str], None] = "reconcile_orm_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'chat_messages' "
            "AND column_name = 'external_message_id'"
        )
    ).scalar() is not None
    if not exists:
        op.add_column(
            "chat_messages",
            sa.Column("external_message_id", sa.String(255), nullable=True),
        )
    idx = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE indexname = 'ix_chat_messages_external_message_id'"
        )
    ).scalar() is not None
    if not idx:
        op.create_index(
            "ix_chat_messages_external_message_id",
            "chat_messages",
            ["external_message_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    idx = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE indexname = 'ix_chat_messages_external_message_id'"
        )
    ).scalar() is not None
    if idx:
        op.drop_index(
            "ix_chat_messages_external_message_id", table_name="chat_messages"
        )
    exists = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'chat_messages' "
            "AND column_name = 'external_message_id'"
        )
    ).scalar() is not None
    if exists:
        op.drop_column("chat_messages", "external_message_id")
