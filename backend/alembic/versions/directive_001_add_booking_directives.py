"""add booking_directives table

Revision ID: directive_001
Revises: waba_booking_toggle
Create Date: 2026-09-14
BookingDirective — structured booking actions (SELECT_DATE etc.)
No impact on existing tables. New table only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "directive_001"
down_revision: Union[str, Sequence[str], None] = "waba_booking_toggle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_directives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("selected_option", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_directives_org", "booking_directives", ["org_id"])
    op.create_index("ix_directives_session", "booking_directives", ["session_id"])
    op.create_index("ix_directives_customer", "booking_directives", ["customer_id"])
    op.create_index("ix_directives_status", "booking_directives", ["status"])
    op.create_index("ix_directives_expires", "booking_directives", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_directives_expires", table_name="booking_directives")
    op.drop_index("ix_directives_status", table_name="booking_directives")
    op.drop_index("ix_directives_customer", table_name="booking_directives")
    op.drop_index("ix_directives_session", table_name="booking_directives")
    op.drop_index("ix_directives_org", table_name="booking_directives")
    op.drop_table("booking_directives")
