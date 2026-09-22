"""add AdminPlus organization hierarchy to organizations

Revision ID: adminplus_hierarchy
Revises: directive_001
Create Date: 2026-09-18

AdminPlus P2 — organization hierarchy ONLY (no auth, no endpoints).

Adds:
  - organizations.kind VARCHAR(20) DEFAULT 'TENANT' (TENANT | ADMINPLUS)
  - organizations.parent_adminplus_id UUID NULLABLE FK -> organizations.id
    ON DELETE SET NULL + index ix_organizations_parent_adminplus

Semantics (matches existing optional-reference convention, e.g.
bookings.attendance_marked_by / rescheduled_from_id):
  - Deleting an AdminPlus org must NEVER cascade-delete its child tenant
    orgs. Children are detached (parent set to NULL) and keep working as
    standalone TENANTs.
  - Existing rows are untouched: server_default backfills kind='TENANT',
    parent stays NULL. No auto-parenting, no promotion.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "adminplus_hierarchy"
down_revision: Union[str, Sequence[str], None] = "directive_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "kind",
            sa.String(20),
            server_default="TENANT",
            nullable=False,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "parent_adminplus_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_organizations_parent_adminplus",
        "organizations",
        "organizations",
        ["parent_adminplus_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_organizations_parent_adminplus",
        "organizations",
        ["parent_adminplus_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organizations_parent_adminplus", table_name="organizations"
    )
    op.drop_constraint(
        "fk_organizations_parent_adminplus",
        "organizations",
        type_="foreignkey",
    )
    op.drop_column("organizations", "parent_adminplus_id")
    op.drop_column("organizations", "kind")
