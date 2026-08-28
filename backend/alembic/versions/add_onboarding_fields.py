"""add_onboarding_fields_to_organizations

Add onboarding_completed flag and industry_type to organizations table.

Revision ID: add_onboarding_fields
Revises: knowledge_context_2026_08
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "add_onboarding_fields"
down_revision: Union[str, Sequence[str], None] = "knowledge_context_2026_08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "organizations",
        sa.Column("industry_type", sa.String(100), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column("custom_prompts", sa.JSON(), nullable=True)
    )
    # Create index for filtering organizations by onboarding status
    op.create_index(
        "ix_organizations_onboarding_completed",
        "organizations",
        ["onboarding_completed"]
    )


def downgrade() -> None:
    op.drop_index("ix_organizations_onboarding_completed", table_name="organizations")
    op.drop_column("organizations", "industry_type")
    op.drop_column("organizations", "onboarding_completed")
    op.drop_column("organizations", "custom_prompts")