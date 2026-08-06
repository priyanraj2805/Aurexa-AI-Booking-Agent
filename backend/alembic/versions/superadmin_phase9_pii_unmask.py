"""superadmin_phase9_pii_unmask

Phase 9 — Hardening (control plane side).

New permission:
  pii.unmask  — see unmasked customer PII (emails, phones, names) across
                control-plane screens. Everything is masked by default;
                this permission + the unmask flag on an endpoint lifts the
                mask for a single response.
Granted to owner and super_admin ONLY — operations/support/auditor always
see masked PII.

Revision ID: superadmin_phase9_pii_unmask
Revises: superadmin_phase8_support_security
Create Date: 2026-08-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase9_pii_unmask"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase8_support_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNMASK_ROLES = ("owner", "super_admin")


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO super_admin.permissions (id, code, module, name, description)
            VALUES (gen_random_uuid(), 'pii.unmask', 'security', 'Unmask PII',
                    'See unmasked customer emails, phones and names')
            ON CONFLICT (code) DO NOTHING
            """
        )
    )
    for role_code in UNMASK_ROLES:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM super_admin.roles r, super_admin.permissions p
                WHERE r.code = :rc AND p.code = 'pii.unmask'
                ON CONFLICT DO NOTHING
                """
            ),
            {"rc": role_code},
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM super_admin.permissions WHERE code = 'pii.unmask'"
    )
