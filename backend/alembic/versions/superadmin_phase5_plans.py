"""superadmin_phase5_plans

Phase 5 — Plans & Billing (backend part 1): plans CRUD + usage-limits
config + plan assignments.

  - super_admin.plans: catalog of subscription plans.
    - code             unique slug (e.g. pro)
    - monthly_price_cents / period (monthly|annual)
    - limits           JSONB usage limits per month; NULL value = unlimited.
      Known keys: bookings_per_month, ai_tokens_per_month, seats, storage_gb.
  - super_admin.plan_assignments: which plan each org is on.
    - one ACTIVE row per org (partial unique index); changing plan revokes
      the current row and inserts a new one, so history is retained.
    - org_id is a real FK to public.organizations (CASCADE on org delete).

Seed data: starter / pro / enterprise plans with sensible limits.

Revision ID: superadmin_phase5_plans
Revises: superadmin_phase4_approvals
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase5_plans"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase4_approvals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# code, name, description, monthly_price_cents, period, limits
DEFAULT_PLANS = [
    (
        "starter",
        "Starter",
        "For small teams getting started",
        0,
        "monthly",
        {"bookings_per_month": 100, "ai_tokens_per_month": 100_000, "seats": 5, "storage_gb": 1},
    ),
    (
        "pro",
        "Pro",
        "For growing businesses",
        9_900,
        "monthly",
        {"bookings_per_month": 1_000, "ai_tokens_per_month": 1_000_000, "seats": 25, "storage_gb": 10},
    ),
    (
        "enterprise",
        "Enterprise",
        "Unlimited scale with priority support",
        49_000,
        "monthly",
        {"bookings_per_month": None, "ai_tokens_per_month": 10_000_000, "seats": 100, "storage_gb": 100},
    ),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.plans (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            monthly_price_cents BIGINT NOT NULL DEFAULT 0,
            period VARCHAR(20) NOT NULL DEFAULT 'monthly',
            limits JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.plan_assignments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id UUID NOT NULL REFERENCES public.organizations (id) ON DELETE CASCADE,
            plan_id UUID NOT NULL REFERENCES super_admin.plans (id) ON DELETE RESTRICT,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            assigned_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            assigned_by_email VARCHAR(255),
            note TEXT,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_plan_assignments_active_org
        ON super_admin.plan_assignments (org_id) WHERE status = 'active'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_plan_assignments_plan
        ON super_admin.plan_assignments (plan_id)
        """
    )

    for code, name, desc, price, period, limits in DEFAULT_PLANS:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.plans
                    (id, code, name, description, is_active, monthly_price_cents, period, limits)
                VALUES
                    (gen_random_uuid(), :code, :name, :desc, TRUE, :price, :period, :limits)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "name": name, "desc": desc, "price": price, "period": period, "limits": __import__("json").dumps(limits)},
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.plan_assignments")
    op.execute("DROP TABLE IF EXISTS super_admin.plans")
