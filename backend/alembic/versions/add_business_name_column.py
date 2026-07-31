"""Add business_name column to org_configs for RAG filtering

Revision ID: add_business_name_column
Revises: add_crawl_id_column
Create Date: 2026-07-30 13:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'add_business_name_column'
down_revision = 'add_crawl_id_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'org_configs',
        sa.Column('business_name', sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_configs', 'business_name')