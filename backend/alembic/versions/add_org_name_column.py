"""Add org_name column to org_configs for editable organization display name

Revision ID: add_org_name_column
Revises: add_crawl_id_column
Create Date: 2026-07-30 12:30:00

Allows admins to set a display name independent of the legal org name
in the organizations table.
"""
from alembic import op
import sqlalchemy as sa


revision = 'add_org_name_column'
down_revision = 'add_crawl_id_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'org_configs',
        sa.Column('org_name', sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_configs', 'org_name')