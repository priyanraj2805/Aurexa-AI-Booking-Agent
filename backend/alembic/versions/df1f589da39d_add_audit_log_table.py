"""add_audit_log_table

Revision ID: df1f589da39d
Revises: fea6fd19bee6
Create Date: 2026-03-28 19:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'df1f589da39d'
down_revision: Union[str, None] = 'fea6fd19bee6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('actor_email', sa.String(length=255), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.String(length=255), nullable=True),
        sa.Column('old_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('new_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    # Individual column indexes
    op.create_index('ix_audit_logs_org_id', 'audit_logs', ['org_id'], unique=False)
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'], unique=False)
    # Composite indexes for common query patterns
    op.create_index('ix_audit_logs_org_created', 'audit_logs', ['org_id', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_org_action', 'audit_logs', ['org_id', 'action'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_audit_logs_org_action', table_name='audit_logs')
    op.drop_index('ix_audit_logs_org_created', table_name='audit_logs')
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
    op.drop_index('ix_audit_logs_action', table_name='audit_logs')
    op.drop_index('ix_audit_logs_org_id', table_name='audit_logs')
    op.drop_table('audit_logs')
