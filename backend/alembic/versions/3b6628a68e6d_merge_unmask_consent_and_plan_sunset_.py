"""merge unmask-consent and plan-sunset branches

Revision ID: 3b6628a68e6d
Revises: superadmin_phase18_unmask_consent, superadmin_phase20_plan_sunset
Create Date: 2026-08-14 14:32:38.752010

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b6628a68e6d'
down_revision: Union[str, Sequence[str], None] = ('superadmin_phase18_unmask_consent', 'superadmin_phase20_plan_sunset')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
