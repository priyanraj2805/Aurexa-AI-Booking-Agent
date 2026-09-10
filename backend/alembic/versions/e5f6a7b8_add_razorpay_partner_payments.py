"""add razorpay partner payments

Revision ID: e5f6a7b8
Revises: 24d4d3942ae4
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = '24d4d3942ae4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # OrgConfig razorpay partner fields
    op.add_column('org_configs', sa.Column('razorpay_submerchant_id', sa.String(length=64), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_public_token', sa.String(length=255), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_access_token_enc', sa.String(length=2048), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_refresh_token_enc', sa.String(length=2048), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_access_expires_at', postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_onboarding_state', sa.String(length=64), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_status', sa.String(length=20), server_default='not_connected', nullable=False))
    op.add_column('org_configs', sa.Column('razorpay_connected_at', postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('org_configs', sa.Column('razorpay_failure_reason', sa.String(length=500), nullable=True))
    op.add_column('org_configs', sa.Column('appointment_price_paise', sa.Integer(), server_default='0', nullable=False))
    op.add_column('org_configs', sa.Column('appointment_currency', sa.String(length=10), server_default='INR', nullable=False))
    op.add_column('org_configs', sa.Column('appointment_price_enabled', sa.Boolean(), server_default='false', nullable=False))

    # Booking payment snapshot
    op.add_column('bookings', sa.Column('payment_status', sa.String(length=20), server_default='not_required', nullable=False))
    op.add_column('bookings', sa.Column('razorpay_order_id', sa.String(length=64), nullable=True))
    op.add_column('bookings', sa.Column('razorpay_payment_id', sa.String(length=64), nullable=True))
    op.add_column('bookings', sa.Column('amount_paise', sa.Integer(), nullable=True))
    op.add_column('bookings', sa.Column('currency', sa.String(length=10), nullable=True))
    op.create_index('ix_bookings_razorpay_order', 'bookings', ['razorpay_order_id'], unique=False)

    # payment_attempts
    op.create_table('payment_attempts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('booking_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('razorpay_order_id', sa.String(length=64), nullable=False),
        sa.Column('razorpay_payment_id', sa.String(length=64), nullable=True),
        sa.Column('razorpay_signature', sa.String(length=255), nullable=True),
        sa.Column('amount_paise', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('verification_status', sa.String(length=20), nullable=False),
        sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('webhook_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('razorpay_order_id')
    )
    op.create_index('ix_payment_attempt_org_created', 'payment_attempts', ['org_id', 'created_at'], unique=False)
    op.create_index('ix_payment_attempt_session', 'payment_attempts', ['session_id', 'status'], unique=False)
    op.create_index('ix_payment_attempt_expires', 'payment_attempts', ['expires_at', 'status'], unique=False)

    # webhook events idempotency
    op.create_table('razorpay_webhook_events',
        sa.Column('event_id', sa.String(length=128), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('processed_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('event_id')
    )


def downgrade() -> None:
    op.drop_table('razorpay_webhook_events')
    op.drop_table('payment_attempts')
    op.drop_index('ix_bookings_razorpay_order', table_name='bookings')
    op.drop_column('bookings', 'currency')
    op.drop_column('bookings', 'amount_paise')
    op.drop_column('bookings', 'razorpay_payment_id')
    op.drop_column('bookings', 'razorpay_order_id')
    op.drop_column('bookings', 'payment_status')
    op.drop_column('org_configs', 'appointment_price_enabled')
    op.drop_column('org_configs', 'appointment_currency')
    op.drop_column('org_configs', 'appointment_price_paise')
    op.drop_column('org_configs', 'razorpay_failure_reason')
    op.drop_column('org_configs', 'razorpay_connected_at')
    op.drop_column('org_configs', 'razorpay_status')
    op.drop_column('org_configs', 'razorpay_onboarding_state')
    op.drop_column('org_configs', 'razorpay_access_expires_at')
    op.drop_column('org_configs', 'razorpay_refresh_token_enc')
    op.drop_column('org_configs', 'razorpay_access_token_enc')
    op.drop_column('org_configs', 'razorpay_public_token')
    op.drop_column('org_configs', 'razorpay_submerchant_id')
