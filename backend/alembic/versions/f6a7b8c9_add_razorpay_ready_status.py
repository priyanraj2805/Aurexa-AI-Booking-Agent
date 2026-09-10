"""add razorpay ready status enum and activated_at

Revision ID: f6a7b8c9
Revises: e5f6a7b8
Create Date: 2026-09-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add activated_at column if not exists (idempotent)
    op.add_column('org_configs', sa.Column('razorpay_activated_at', postgresql.TIMESTAMP(timezone=True), nullable=True))
    # Normalize legacy statuses to new enum
    # not_connected -> NOT_CONNECTED, connecting/under_review -> UNDER_REVIEW, connected -> READY, needs_clarification -> ACTION_REQUIRED, failed -> REJECTED
    op.execute("""
        UPDATE org_configs SET razorpay_status = CASE
            WHEN lower(razorpay_status) IN ('not_connected') THEN 'NOT_CONNECTED'
            WHEN lower(razorpay_status) IN ('connecting','under_review') THEN 'UNDER_REVIEW'
            WHEN lower(razorpay_status) IN ('connected') THEN 'READY'
            WHEN lower(razorpay_status) IN ('needs_clarification') THEN 'ACTION_REQUIRED'
            WHEN lower(razorpay_status) IN ('failed') THEN 'REJECTED'
            WHEN lower(razorpay_status) IN ('suspended') THEN 'SUSPENDED'
            WHEN lower(razorpay_status) IN ('rejected') THEN 'REJECTED'
            ELSE razorpay_status
        END
        WHERE razorpay_status IS NOT NULL
    """)
    # Ensure default is NOT_CONNECTED for new rows
    op.alter_column('org_configs', 'razorpay_status', server_default='NOT_CONNECTED')

def downgrade() -> None:
    op.drop_column('org_configs', 'razorpay_activated_at')
    op.execute("""
        UPDATE org_configs SET razorpay_status = CASE
            WHEN razorpay_status = 'NOT_CONNECTED' THEN 'not_connected'
            WHEN razorpay_status = 'UNDER_REVIEW' THEN 'under_review'
            WHEN razorpay_status = 'READY' THEN 'connected'
            WHEN razorpay_status = 'ACTION_REQUIRED' THEN 'needs_clarification'
            WHEN razorpay_status = 'REJECTED' THEN 'failed'
            WHEN razorpay_status = 'SUSPENDED' THEN 'suspended'
            ELSE lower(razorpay_status)
        END
    """)
