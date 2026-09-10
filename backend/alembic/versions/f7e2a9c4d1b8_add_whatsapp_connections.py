"""add whatsapp_connections (QR/Baileys transport registry)

Revision ID: f7e2a9c4d1b8
Revises: d9e4f2a8
Create Date: 2026-09-10

One row per organization max. Owns org ownership + status only;
Baileys auth state lives in the wa-gateway volume, not here.
Legacy Meta Cloud API columns on org_configs are untouched by this migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f7e2a9c4d1b8"
down_revision: Union[str, Sequence[str], None] = "d9e4f2a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="PENDING_QR", nullable=False),
        sa.Column("connected_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("disconnected_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_whatsapp_conn_org"),
    )
    op.create_index("ix_whatsapp_conn_org", "whatsapp_connections", ["org_id"], unique=False)
    op.create_index("ix_whatsapp_conn_status", "whatsapp_connections", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_whatsapp_conn_status", table_name="whatsapp_connections")
    op.drop_index("ix_whatsapp_conn_org", table_name="whatsapp_connections")
    op.drop_table("whatsapp_connections")
