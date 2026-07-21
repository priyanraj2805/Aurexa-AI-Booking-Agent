"""add escalation policy and alert acknowledgement fields

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_alerts",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "system_alerts",
        sa.Column("acknowledged_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "system_alerts",
        sa.Column("acknowledgement_note", sa.String(length=500), nullable=True),
    )

    op.create_table(
        "escalation_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id"),
    )
    op.create_index(op.f("ix_escalation_policies_org_id"), "escalation_policies", ["org_id"], unique=False)

    op.create_table(
        "escalation_routing_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_value", sa.String(length=255), nullable=False),
        sa.Column("order_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["escalation_policies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "order_index", name="uq_target_policy_order"),
    )
    op.create_index(
        op.f("ix_escalation_routing_targets_policy_id"),
        "escalation_routing_targets",
        ["policy_id"],
        unique=False,
    )

    op.create_table(
        "escalation_rotation_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_hour_utc", sa.Integer(), nullable=False),
        sa.Column("end_hour_utc", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["escalation_policies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "order_index", name="uq_rotation_policy_order"),
    )
    op.create_index(
        op.f("ix_escalation_rotation_windows_policy_id"),
        "escalation_rotation_windows",
        ["policy_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_escalation_rotation_windows_policy_id"), table_name="escalation_rotation_windows")
    op.drop_table("escalation_rotation_windows")
    op.drop_index(op.f("ix_escalation_routing_targets_policy_id"), table_name="escalation_routing_targets")
    op.drop_table("escalation_routing_targets")
    op.drop_index(op.f("ix_escalation_policies_org_id"), table_name="escalation_policies")
    op.drop_table("escalation_policies")

    op.drop_column("system_alerts", "acknowledgement_note")
    op.drop_column("system_alerts", "acknowledged_by_user_id")
    op.drop_column("system_alerts", "acknowledged_at")
