"""superadmin_phase20_plan_sunset

Plan sunset (deletion grace period): when a super admin deletes a plan that
organizations are still assigned to, those organizations keep the plan until
their billing validity ends. The grace end is stored on each active assignment
as `expires_at`:

  - paid orgs  -> subscription.current_period_end
  - free orgs  -> end of the current month (synthetic validity)

A lazy settle step (on plan/billing reads) revokes assignments whose
`expires_at` has passed, so the org ends up with no plan at the deleted
plan's price afterwards.

Revision ID: superadmin_phase20_plan_sunset
Revises: superadmin_phase19_password_reset
Create Date: 2026-08-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase20_plan_sunset"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase19_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plan_assignments",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="super_admin",
    )
    op.create_index(
        "ix_plan_assignments_status_expires",
        "plan_assignments",
        ["status", "expires_at"],
        schema="super_admin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_plan_assignments_status_expires",
        table_name="plan_assignments",
        schema="super_admin",
    )
    op.drop_column("plan_assignments", "expires_at", schema="super_admin")