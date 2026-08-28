"""add_custom_prompts_to_organizations

Add custom_prompts JSON column to organizations table.

Revision ID: add_custom_prompts
Revises: add_onboarding_fields
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "add_custom_prompts"
down_revision: Union[str, Sequence[str], None] = "add_onboarding_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("custom_prompts", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("organizations", "custom_prompts")