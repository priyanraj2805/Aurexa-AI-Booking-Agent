"""remove AdminPlus organization hierarchy from organizations

Revision ID: remove_adminplus_hierarchy
Revises: adminplus_hierarchy
Create Date: 2026-09-22

Removes the AdminPlus hierarchy columns added in adminplus_hierarchy:
  - organizations.kind
  - organizations.parent_adminplus_id (FK + index)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "remove_adminplus_hierarchy"
down_revision: Union[str, Sequence[str], None] = "adminplus_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
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