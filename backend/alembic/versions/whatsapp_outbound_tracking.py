"""whatsapp outbound tracking - chat_messages send_status etc.

Revision ID: whatsapp_outbound_tracking
Revises: whatsapp_8h_expiry
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "whatsapp_outbound_tracking"
down_revision: Union[str, Sequence[str], None] = "whatsapp_8h_expiry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("channel", sa.String(length=20), nullable=True))
    op.add_column("chat_messages", sa.Column("direction", sa.String(length=10), nullable=True))
    op.add_column("chat_messages", sa.Column("send_status", sa.String(length=20), nullable=True))
    op.add_column("chat_messages", sa.Column("provider_message_id", sa.String(length=255), nullable=True))
    op.add_column("chat_messages", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_messages", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_messages", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_messages", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_messages", sa.Column("error_message", sa.Text(), nullable=True))
    op.create_index("ix_msg_send_status", "chat_messages", ["send_status"])
    op.create_index("ix_msg_provider_id", "chat_messages", ["provider_message_id"])
    op.create_index("ix_msg_channel", "chat_messages", ["channel"])
    # Existing rows: mark user messages as inbound, agent as outbound PENDING for audit
    op.execute("UPDATE chat_messages SET direction = CASE WHEN role='user' THEN 'inbound' ELSE 'outbound' END WHERE direction IS NULL")
    op.execute("UPDATE chat_messages SET send_status = CASE WHEN role='user' THEN NULL WHEN sender_type='human' THEN 'SENT' ELSE 'GENERATED' END WHERE send_status IS NULL AND role='agent'")


def downgrade() -> None:
    op.drop_index("ix_msg_channel", table_name="chat_messages")
    op.drop_index("ix_msg_provider_id", table_name="chat_messages")
    op.drop_index("ix_msg_send_status", table_name="chat_messages")
    op.drop_column("chat_messages", "error_message")
    op.drop_column("chat_messages", "failed_at")
    op.drop_column("chat_messages", "read_at")
    op.drop_column("chat_messages", "delivered_at")
    op.drop_column("chat_messages", "sent_at")
    op.drop_column("chat_messages", "provider_message_id")
    op.drop_column("chat_messages", "send_status")
    op.drop_column("chat_messages", "direction")
    op.drop_column("chat_messages", "channel")
