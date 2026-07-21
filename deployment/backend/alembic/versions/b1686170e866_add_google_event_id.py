"""Add google_event_id and google_calendar_refresh_token

Revision ID: b1686170e866
Revises: 4fe08d123873
Create Date: 2026-03-23 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1686170e866"
down_revision: Union[str, Sequence[str], None] = "4fe08d123873"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bookings", sa.Column("google_event_id", sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("bookings", "google_event_id")
