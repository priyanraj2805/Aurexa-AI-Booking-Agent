"""Add locked_until column to time_slots

Revision ID: f96fa515c57d
Revises: 4bad9104aa03
Create Date: 2026-03-24 20:03:44.202879

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f96fa515c57d"
down_revision: Union[str, Sequence[str], None] = "4bad9104aa03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "time_slots",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("time_slots", "locked_until")
