"""add attendance fields

Revision ID: 24d4d3942ae4
Revises: c3d4e5f6
Create Date: 2026-09-03 04:56:36.074000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '24d4d3942ae4'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bookings', sa.Column('attendance_status', sa.String(length=20), server_default='SCHEDULED', nullable=False))
    op.add_column('bookings', sa.Column('attended_at', postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('bookings', sa.Column('attendance_marked_by', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('bookings', sa.Column('attendance_notes', sa.String(length=500), nullable=True))
    op.add_column('bookings', sa.Column('rescheduled_from_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('bookings', sa.Column('cancellation_reason', sa.String(length=500), nullable=True))
    op.create_index('ix_booking_attendance', 'bookings', ['org_id', 'attendance_status'], unique=False)
    op.create_foreign_key('fk_booking_attendance_marked_by', 'bookings', 'users', ['attendance_marked_by'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_booking_rescheduled_from', 'bookings', 'bookings', ['rescheduled_from_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_booking_rescheduled_from', 'bookings', type_='foreignkey')
    op.drop_constraint('fk_booking_attendance_marked_by', 'bookings', type_='foreignkey')
    op.drop_index('ix_booking_attendance', table_name='bookings')
    op.drop_column('bookings', 'cancellation_reason')
    op.drop_column('bookings', 'rescheduled_from_id')
    op.drop_column('bookings', 'attendance_notes')
    op.drop_column('bookings', 'attendance_marked_by')
    op.drop_column('bookings', 'attended_at')
    op.drop_column('bookings', 'attendance_status')
