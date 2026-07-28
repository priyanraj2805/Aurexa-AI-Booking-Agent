"""Add website crawling and document upload features

Revision ID: 20240727_add_rag_tables
Revises: 6f1a2b3c4d5e
Create Date: 2024-07-27 17:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20240727_add_rag_tables'
down_revision = '6f1a2b3c4d5e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to org_configs
    op.add_column('org_configs', sa.Column('website_url', sa.Text(), nullable=True))
    op.add_column('org_configs', sa.Column('crawl_status', sa.String(50), nullable=True, default='idle'))
    op.add_column('org_configs', sa.Column('crawl_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('org_configs', sa.Column('crawl_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('org_configs', sa.Column('total_pages_crawled', sa.Integer(), nullable=True, default=0))
    op.add_column('org_configs', sa.Column('uploaded_docs', postgresql.JSONB(astext_type=sa.Text()), nullable=True, default='[]'))
    
    # Create document_chunks table
    op.create_table(
        'document_chunks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('doc_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('source_type', sa.String(20), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True, default='{}'),
        sa.Column('tsvector_col', postgresql.TSVECTOR, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    
    # Create indexes
    op.create_index('idx_chunks_fts', 'document_chunks', [sa.text('tsvector_col')], postgresql_using='gin')
    op.create_index('idx_chunks_org', 'document_chunks', ['org_id'])
    op.create_index('idx_chunks_source', 'document_chunks', ['source_type'])


def downgrade() -> None:
    op.drop_index('idx_chunks_source', table_name='document_chunks')
    op.drop_index('idx_chunks_org', table_name='document_chunks')
    op.drop_index('idx_chunks_fts', table_name='document_chunks', postgresql_using='gin')
    op.drop_table('document_chunks')
    
    op.drop_column('org_configs', 'uploaded_docs')
    op.drop_column('org_configs', 'total_pages_crawled')
    op.drop_column('org_configs', 'crawl_completed_at')
    op.drop_column('org_configs', 'crawl_started_at')
    op.drop_column('org_configs', 'crawl_status')
    op.drop_column('org_configs', 'website_url')