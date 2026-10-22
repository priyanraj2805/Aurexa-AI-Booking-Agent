"""Drop the WhatsApp engagement analytics tables (leads, booking funnel).

The engagement dashboard (leads, booking funnel, opt-outs, waitlist cards in
the admin panel) was removed. The opt-out table and the delivery log stay:
opt-out is a Meta compliance requirement (a STOP must be honoured or the
business number gets banned) and the delivery log dedupes every template
send so customers do not get duplicate messages.

Revision ID: drop_engagement_analytics
Revises: whatsapp_engagement
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "drop_engagement_analytics"
down_revision: Union[str, Sequence[str], None] = "whatsapp_engagement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Indexes that belong to the dropped tables, dropped with them.
_DROPPED_INDEXES = (
    "ix_booking_lead_org_status",
    "ix_booking_lead_org_created",
    "ix_booking_leads_org_id",
    "ix_booking_leads_session_id",
    "ix_booking_leads_phone",
    "ix_booking_funnel_org_step",
    "ix_booking_funnel_org_created",
    "ix_booking_funnel_events_org_id",
    "ix_booking_funnel_events_session_id",
)


def upgrade() -> None:
    for idx in _DROPPED_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS booking_leads")
    op.execute("DROP TABLE IF EXISTS booking_funnel_events")


def downgrade() -> None:
    import sqlalchemy as sa
    from sqlalchemy.dialects import postgresql

    op.create_table(
        "booking_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="whatsapp"),
        sa.Column("intent", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_booking_leads_org_id", "booking_leads", ["org_id"])
    op.create_index("ix_booking_leads_session_id", "booking_leads", ["session_id"])
    op.create_index("ix_booking_leads_phone", "booking_leads", ["phone"])
    op.create_index("ix_booking_lead_org_status", "booking_leads", ["org_id", "status"])
    op.create_index("ix_booking_lead_org_created", "booking_leads", ["org_id", "created_at"])

    op.create_table(
        "booking_funnel_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="whatsapp"),
        sa.Column("step", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False, server_default="entered"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_booking_funnel_events_org_id", "booking_funnel_events", ["org_id"])
    op.create_index("ix_booking_funnel_events_session_id", "booking_funnel_events", ["session_id"])
    op.create_index("ix_booking_funnel_org_step", "booking_funnel_events", ["org_id", "step"])
    op.create_index("ix_booking_funnel_org_created", "booking_funnel_events", ["org_id", "created_at"])
