"""superadmin_phase5_billing

Phase 5 — Plans & Billing (backend part 2): subscriptions, invoices and
revenue reports.

  - super_admin.subscriptions: one non-canceled row per org (partial unique
    index). Provider-agnostic: provider column names the billing provider,
    provider_sub_id holds the external identifier.
  - super_admin.invoices: monthly invoices per org.
    status: open → paid | refunded | void | uncollectible
  - New permission billing.manage (create subscriptions/invoices) granted
    to owner, super_admin and operations roles.

Revision ID: superadmin_phase5_billing
Revises: superadmin_phase5_plans
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase5_billing"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase5_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MANAGE_ROLES = ("owner", "super_admin", "operations")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES super_admin.plans (id) ON DELETE RESTRICT,
            provider VARCHAR(50) NOT NULL DEFAULT 'mock',
            provider_customer_id VARCHAR(255),
            provider_sub_id VARCHAR(255),
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMPTZ NOT NULL DEFAULT now(),
            current_period_end TIMESTAMPTZ NOT NULL DEFAULT now() + interval '1 month',
            cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
            canceled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_subscriptions_active_org
        ON super_admin.subscriptions (org_id)
        WHERE status IN ('active', 'past_due', 'trialing')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriptions_plan
        ON super_admin.subscriptions (plan_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.invoices (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            subscription_id UUID REFERENCES super_admin.subscriptions (id) ON DELETE SET NULL,
            provider VARCHAR(50) NOT NULL DEFAULT 'mock',
            provider_invoice_id VARCHAR(255) UNIQUE,
            number VARCHAR(50) NOT NULL,
            amount_cents BIGINT NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'usd',
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            invoice_url TEXT,
            description TEXT,
            due_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            refunded_at TIMESTAMPTZ,
            refund_amount_cents BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_invoices_org
        ON super_admin.invoices (org_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_invoices_status
        ON super_admin.invoices (status, due_at)
        """
    )

    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO super_admin.permissions (id, code, module, name, description)
            VALUES (gen_random_uuid(), 'billing.manage', 'billing', 'Manage Billing',
                    'Create subscriptions and issue invoices')
            ON CONFLICT (code) DO NOTHING
            """
        )
    )
    for role_code in MANAGE_ROLES:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM super_admin.roles r, super_admin.permissions p
                WHERE r.code = :rc AND p.code = 'billing.manage'
                ON CONFLICT DO NOTHING
                """
            ),
            {"rc": role_code},
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.invoices")
    op.execute("DROP TABLE IF EXISTS super_admin.subscriptions")
    op.execute("DELETE FROM super_admin.permissions WHERE code = 'billing.manage'")
