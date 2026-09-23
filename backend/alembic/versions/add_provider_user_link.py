"""link providers to users for member dashboard (own bookings)

Revision ID: add_provider_user_link
Revises: remove_adminplus_hierarchy
Create Date: 2026-09-22

Adds providers.user_id nullable FK -> users.id ON DELETE SET NULL
so a Member (provider) can have its own login (role-based dashboard).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "add_provider_user_link"
down_revision: Union[str, Sequence[str], None] = "remove_adminplus_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent — manual hotfix already added the column on live DB.
    # Wrap each step so `alembic upgrade head` never 500s on re-run.
    try:
        op.add_column(
            "providers",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    except Exception:
        pass
    try:
        op.create_foreign_key(
            "fk_providers_user",
            "providers",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    except Exception:
        pass
    try:
        op.create_index("ix_providers_user", "providers", ["user_id"])
    except Exception:
        pass
    try:
        op.create_index("ix_providers_org_user", "providers", ["org_id", "user_id"])
    except Exception:
        pass


def downgrade() -> None:
    op.drop_index("ix_providers_org_user", table_name="providers")
    op.drop_index("ix_providers_user", table_name="providers")
    op.drop_constraint("fk_providers_user", "providers", type_="foreignkey")
    op.drop_column("providers", "user_id")
