"""WhatsApp engagement tables: opt-outs, leads, waitlist, delivery log, funnel.

Revision ID: whatsapp_engagement
Revises: cashfree_link_checkout_url
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "whatsapp_engagement"
down_revision: Union[str, Sequence[str], None] = "cashfree_link_checkout_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── opt-outs ──
    op.create_table(
        "whatsapp_opt_outs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="whatsapp"),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("org_id", "phone", "channel", name="uq_wa_optout_org_phone"),
    )
    op.create_index("ix_whatsapp_opt_outs_org_id", "whatsapp_opt_outs", ["org_id"])
    op.create_index("ix_whatsapp_opt_outs_phone", "whatsapp_opt_outs", ["phone"])
    op.create_index("ix_wa_optout_org_created", "whatsapp_opt_outs", ["org_id", "created_at"])

    # ── leads ──
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

    # ── waitlist ──
    op.create_table(
        "booking_waitlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="waiting"),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unit_id"], ["service_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_booking_waitlist_org_id", "booking_waitlist", ["org_id"])
    op.create_index("ix_booking_waitlist_session_id", "booking_waitlist", ["session_id"])
    op.create_index("ix_booking_waitlist_phone", "booking_waitlist", ["phone"])
    op.create_index("ix_booking_waitlist_org_status", "booking_waitlist", ["org_id", "status"])
    op.create_index("ix_booking_waitlist_org_date", "booking_waitlist", ["org_id", "target_date"])

    # ── delivery log ──
    op.create_table(
        "whatsapp_delivery_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("template_name", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="sent"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("booking_id", "channel", name="uq_wa_delivery_booking_channel"),
    )
    op.create_index("ix_whatsapp_delivery_logs_org_id", "whatsapp_delivery_logs", ["org_id"])
    op.create_index("ix_whatsapp_delivery_logs_phone", "whatsapp_delivery_logs", ["phone"])
    op.create_index("ix_wa_delivery_org_created", "whatsapp_delivery_logs", ["org_id", "created_at"])

    # ── funnel ──
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


def downgrade() -> None:
    op.drop_index("ix_booking_funnel_org_created", table_name="booking_funnel_events")
    op.drop_index("ix_booking_funnel_org_step", table_name="booking_funnel_events")
    op.drop_index("ix_booking_funnel_events_session_id", table_name="booking_funnel_events")
    op.drop_index("ix_booking_funnel_events_org_id", table_name="booking_funnel_events")
    op.drop_table("booking_funnel_events")

    op.drop_index("ix_wa_delivery_org_created", table_name="whatsapp_delivery_logs")
    op.drop_index("ix_whatsapp_delivery_logs_phone", table_name="whatsapp_delivery_logs")
    op.drop_index("ix_whatsapp_delivery_logs_org_id", table_name="whatsapp_delivery_logs")
    op.drop_table("whatsapp_delivery_logs")

    op.drop_index("ix_booking_waitlist_org_date", table_name="booking_waitlist")
    op.drop_index("ix_booking_waitlist_org_status", table_name="booking_waitlist")
    op.drop_index("ix_booking_waitlist_phone", table_name="booking_waitlist")
    op.drop_index("ix_booking_waitlist_session_id", table_name="booking_waitlist")
    op.drop_index("ix_booking_waitlist_org_id", table_name="booking_waitlist")
    op.drop_table("booking_waitlist")

    op.drop_index("ix_booking_lead_org_created", table_name="booking_leads")
    op.drop_index("ix_booking_lead_org_status", table_name="booking_leads")
    op.drop_index("ix_booking_leads_phone", table_name="booking_leads")
    op.drop_index("ix_booking_leads_session_id", table_name="booking_leads")
    op.drop_index("ix_booking_leads_org_id", table_name="booking_leads")
    op.drop_table("booking_leads")

    op.drop_index("ix_wa_optout_org_created", table_name="whatsapp_opt_outs")
    op.drop_index("ix_whatsapp_opt_outs_phone", table_name="whatsapp_opt_outs")
    op.drop_index("ix_whatsapp_opt_outs_org_id", table_name="whatsapp_opt_outs")
    op.drop_table("whatsapp_opt_outs")
