"""add_email_delivery_and_system_alert_tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6,df1f589da39d,5cebb881d323
Create Date: 2026-03-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = (
    "a1b2c3d4e5f6",
    "df1f589da39d",
    "5cebb881d323",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_delivery_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_delivery_logs_org_id", "email_delivery_logs", ["org_id"], unique=False)
    op.create_index(
        "ix_email_delivery_logs_recipient_email",
        "email_delivery_logs",
        ["recipient_email"],
        unique=False,
    )
    op.create_index(
        "ix_email_delivery_org_created",
        "email_delivery_logs",
        ["org_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_delivery_org_status_created",
        "email_delivery_logs",
        ["org_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_delivery_retry_scan",
        "email_delivery_logs",
        ["next_retry_at", "status"],
        unique=False,
    )

    op.create_table(
        "system_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("component", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_alerts_org_id", "system_alerts", ["org_id"], unique=False)
    op.create_index("ix_system_alerts_code", "system_alerts", ["code"], unique=False)
    op.create_index("ix_system_alerts_dedupe_key", "system_alerts", ["dedupe_key"], unique=False)
    op.create_index(
        "ix_system_alert_component_severity_created",
        "system_alerts",
        ["component", "severity", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_alert_active_dedupe",
        "system_alerts",
        ["dedupe_key", "resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_system_alert_active_dedupe", table_name="system_alerts")
    op.drop_index("ix_system_alert_component_severity_created", table_name="system_alerts")
    op.drop_index("ix_system_alerts_dedupe_key", table_name="system_alerts")
    op.drop_index("ix_system_alerts_code", table_name="system_alerts")
    op.drop_index("ix_system_alerts_org_id", table_name="system_alerts")
    op.drop_table("system_alerts")

    op.drop_index("ix_email_delivery_retry_scan", table_name="email_delivery_logs")
    op.drop_index("ix_email_delivery_org_status_created", table_name="email_delivery_logs")
    op.drop_index("ix_email_delivery_org_created", table_name="email_delivery_logs")
    op.drop_index("ix_email_delivery_logs_recipient_email", table_name="email_delivery_logs")
    op.drop_index("ix_email_delivery_logs_org_id", table_name="email_delivery_logs")
    op.drop_table("email_delivery_logs")

