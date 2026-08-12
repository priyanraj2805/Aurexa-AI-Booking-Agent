"""tenant_broadcast_deliveries

Delivers super-admin broadcasts to org admins.

When a super admin sends a broadcast (all orgs or selected orgs), the
super-admin backend writes one delivery row per target organization here.
Tenant admins then see their org's deliveries in the admin panel and mark
them read per user.

  - `public.broadcast_deliveries`: one row per (broadcast, org) — the inbox
    row surfaced to every admin of that org.
  - `public.broadcast_reads`: per-user read state for a delivery.

Revision ID: tenant_broadcast_deliveries
Revises: superadmin_phase17_peek_consent
Create Date: 2026-08-12

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "tenant_broadcast_deliveries"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase17_peek_consent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.broadcast_deliveries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            broadcast_id UUID NOT NULL
                REFERENCES super_admin.broadcast_notifications (id) ON DELETE CASCADE,
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (broadcast_id, org_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_broadcast_deliveries_inbox
        ON public.broadcast_deliveries (org_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.broadcast_reads (
            delivery_id UUID NOT NULL
                REFERENCES public.broadcast_deliveries (id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
            read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (delivery_id, user_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.broadcast_reads")
    op.execute("DROP INDEX IF EXISTS ix_broadcast_deliveries_inbox")
    op.execute("DROP TABLE IF EXISTS public.broadcast_deliveries")
