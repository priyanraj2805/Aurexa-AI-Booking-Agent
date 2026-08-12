"""superadmin_phase13_handshake_single_use

Adds handshake_used_at to public.impersonation_sessions so the control-plane
"Open console" handshake ticket is single-use: impersonate/enter claims the
ticket atomically (UPDATE ... WHERE handshake_used_at IS NULL), so a copied
or replayed handshake link stops working immediately after the first use.

Revision ID: superadmin_phase13_handshake_single_use
Revises: superadmin_phase12_support_tickets
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase13_handshake_single_use"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase12_support_tickets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.impersonation_sessions
        ADD COLUMN IF NOT EXISTS handshake_used_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.impersonation_sessions DROP COLUMN IF EXISTS handshake_used_at"
    )
