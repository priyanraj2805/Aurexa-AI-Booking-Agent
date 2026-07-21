"""add timezone scheduling policy fields to org config

Revision ID: 15a_tz_sched_policy_fields
Revises: 17a_widget_token
Create Date: 2026-04-19 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "15a_tz_sched_policy_fields"
down_revision: Union[str, Sequence[str], None] = "17a_widget_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "org_configs",
        sa.Column(
            "allow_same_day_booking",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "org_configs",
        sa.Column(
            "slot_cutoff_minutes",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("org_configs", "slot_cutoff_minutes")
    op.drop_column("org_configs", "allow_same_day_booking")
