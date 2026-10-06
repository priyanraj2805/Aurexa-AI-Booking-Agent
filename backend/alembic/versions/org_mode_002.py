"""org mode phase 2: staff invites + unit scopes

Revision ID: org_mode_002
Revises: org_mode_001

  - staff_invites: email invite to join SAME org (no new org).
    token stored as sha256 hash, 24h expiry, single use.
  - staff_unit_scopes: dept_manager limited to units (rooms he manages).
    provider link stays on providers.user_id.

Additive only.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "org_mode_002"
down_revision: Union[str, Sequence[str], None] = "org_mode_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staff_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role_code", sa.String(80), nullable=False, server_default="staff"),
        sa.Column("unit_ids", postgresql.JSONB(), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staff_invites_org", "staff_invites", ["org_id"])
    op.create_index("ix_staff_invites_email", "staff_invites", ["email"])

    op.create_table(
        "staff_unit_scopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unit_id"], ["service_units.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "unit_id", name="uq_staff_scope"),
    )
    op.create_index("ix_staff_scopes_user", "staff_unit_scopes", ["user_id"])
    op.create_index("ix_staff_scopes_org", "staff_unit_scopes", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_staff_scopes_org", table_name="staff_unit_scopes")
    op.drop_index("ix_staff_scopes_user", table_name="staff_unit_scopes")
    op.drop_table("staff_unit_scopes")
    op.drop_index("ix_staff_invites_email", table_name="staff_invites")
    op.drop_index("ix_staff_invites_org", table_name="staff_invites")
    op.drop_table("staff_invites")
