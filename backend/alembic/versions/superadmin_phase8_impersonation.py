"""superadmin_phase8_impersonation

Phase 8 — Support & Security (tenant side): impersonation sessions.

  - public.impersonation_sessions: one row per issued impersonation token.
    get_current_user rejects impersonation JWTs whose session is revoked or
    expired, so a revoked impersonation dies immediately even though the
    underlying JWT is stateless. "Single active session" is enforced by the
    control plane revoking the previous session before issuing a new one.

Revision ID: superadmin_phase8_impersonation
Revises: superadmin_phase7_ops_tools
Create Date: 2026-08-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase8_impersonation"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase7_ops_tools"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.impersonation_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            user_email VARCHAR(255) NOT NULL,
            token_jti VARCHAR(64) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            revoked_by VARCHAR(255)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_impersonation_sessions_org
        ON public.impersonation_sessions (org_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.impersonation_sessions")
