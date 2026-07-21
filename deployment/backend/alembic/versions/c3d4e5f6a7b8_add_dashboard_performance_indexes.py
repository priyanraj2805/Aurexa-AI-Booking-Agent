"""add_dashboard_performance_indexes

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(inspector, table_name: str, index_name: str) -> bool:
    return any(ix.get("name") == index_name for ix in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _index_exists(inspector, "chat_sessions", "ix_session_org_updated"):
        op.create_index(
            "ix_session_org_updated",
            "chat_sessions",
            ["org_id", "updated_at"],
            unique=False,
        )

    if not _index_exists(inspector, "bookings", "ix_booking_org_created"):
        op.create_index(
            "ix_booking_org_created",
            "bookings",
            ["org_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _index_exists(inspector, "chat_sessions", "ix_session_org_updated"):
        op.drop_index("ix_session_org_updated", table_name="chat_sessions")
    if _index_exists(inspector, "bookings", "ix_booking_org_created"):
        op.drop_index("ix_booking_org_created", table_name="bookings")

