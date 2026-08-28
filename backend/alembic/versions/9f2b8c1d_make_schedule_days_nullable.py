"""make schedule_days times nullable for closed days

Revision ID: 9f2b8c1d
Revises: add_custom_prompts
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "9f2b8c1d"
down_revision: Union[str, Sequence[str], None] = "add_custom_prompts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("schedule_days", "open_time", existing_type=sa.Time(), nullable=True)
    op.alter_column("schedule_days", "close_time", existing_type=sa.Time(), nullable=True)


def downgrade() -> None:
    op.alter_column("schedule_days", "close_time", existing_type=sa.Time(), nullable=False)
    op.alter_column("schedule_days", "open_time", existing_type=sa.Time(), nullable=False)
