"""Add crawl_id column to org_configs

Revision ID: add_crawl_id_column
Revises: 20240727_add_rag_tables
Create Date: 2026-07-30 12:00:00

Persisting the Firecrawl crawl_id on org_configs lets the admin UI restore
the in-progress crawl state after a page refresh.
"""
from alembic import op
import sqlalchemy as sa


revision = 'add_crawl_id_column'
down_revision = '20240727_add_rag_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'org_configs',
        sa.Column('crawl_id', sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_configs', 'crawl_id')
