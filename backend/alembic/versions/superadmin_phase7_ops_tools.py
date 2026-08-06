"""superadmin_phase7_ops_tools

Phase 7 — Ops tools (backend): DB-backed feature flag registry with
scoping + canary rollout + kill switch, and broadcast notifications with a
draft -> reviewed -> sent workflow.

  - super_admin.feature_flags: platform/org/plan scoped flags.
      * enabled=false  -> OFF everywhere (kill switch)
      * scope platform -> everyone (subject to rollout_percent)
      * scope org      -> only targeting.org_ids (subject to rollout_percent)
      * scope plan     -> only orgs whose active plan is in targeting.plan_codes
      * rollout_percent -> canary: stable hash of (code, org) < percent
  - super_admin.broadcast_notifications: drafts -> review -> send/cancel
    with audience (all_orgs | selected_orgs | staff) and audit trail.
  - New permissions:
      flags.view, flags.manage, notifications.view, notifications.manage
    owner/super_admin get all; operations gets the read-only pair;
    support gets nothing.
  - Seeded flags: chatbot.enabled, chatbot.canary, crawler.firecrawl.

Revision ID: superadmin_phase7_ops_tools
Revises: superadmin_phase6_analytics
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase7_ops_tools"
down_revision: Union[str, Sequence[str], None] = "superadmin_phase6_analytics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_PERMS = (
    "flags.view",
    "flags.manage",
    "notifications.view",
    "notifications.manage",
)
READ_ONLY_PERMS = ("flags.view", "notifications.view")

PERM_META = {
    "flags.view": ("flags", "View Feature Flags", "Read the feature flag registry"),
    "flags.manage": ("flags", "Manage Feature Flags", "Create, toggle, scope and delete feature flags"),
    "notifications.view": ("notifications", "View Notifications", "Read broadcast notifications"),
    "notifications.manage": ("notifications", "Manage Notifications", "Create, review, send and cancel broadcast notifications"),
}


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.feature_flags (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(100) NOT NULL UNIQUE,
            name VARCHAR(150) NOT NULL,
            description TEXT,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            scope VARCHAR(16) NOT NULL DEFAULT 'platform'
                CHECK (scope IN ('platform', 'org', 'plan')),
            targeting JSONB NOT NULL DEFAULT '{}'::jsonb,
            rollout_percent SMALLINT NOT NULL DEFAULT 100
                CHECK (rollout_percent BETWEEN 0 AND 100),
            updated_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_feature_flags_scope_enabled
        ON super_admin.feature_flags (scope, enabled)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.broadcast_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title VARCHAR(255) NOT NULL,
            body TEXT NOT NULL,
            audience VARCHAR(20) NOT NULL DEFAULT 'all_orgs'
                CHECK (audience IN ('all_orgs', 'selected_orgs', 'staff')),
            org_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'reviewed', 'sent', 'cancelled')),
            created_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            reviewed_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            reviewed_at TIMESTAMPTZ,
            sent_by_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            sent_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_broadcast_notifications_status_created
        ON super_admin.broadcast_notifications (status, created_at DESC)
        """
    )

    for code, (module, name, description) in PERM_META.items():
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.permissions (id, code, module, name, description)
                VALUES (gen_random_uuid(), :code, :module, :name, :description)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "module": module, "name": name, "description": description},
        )
    for role_code, codes in (
        ("owner", ALL_PERMS),
        ("super_admin", ALL_PERMS),
        ("operations", READ_ONLY_PERMS),
    ):
        for code in codes:
            op.get_bind().execute(
                sa.text(
                    """
                    INSERT INTO super_admin.role_permissions (role_id, permission_id)
                    SELECT r.id, p.id
                    FROM super_admin.roles r, super_admin.permissions p
                    WHERE r.code = :rc AND p.code = :pc
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"rc": role_code, "pc": code},
            )

    # Seed demo flags (platform-scoped; safe defaults for the tenant product)
    op.execute(
        """
        INSERT INTO super_admin.feature_flags (id, code, name, description, enabled, scope)
        VALUES
            (gen_random_uuid(), 'chatbot.enabled', 'Chatbot',
             'Master switch for the AI booking chatbot across all orgs', TRUE, 'platform'),
            (gen_random_uuid(), 'chatbot.canary', 'Chatbot canary',
             'Canary rollout of the latest chatbot build (5% of orgs)', TRUE, 'platform'),
            (gen_random_uuid(), 'crawler.firecrawl', 'Firecrawl crawler',
             'Allow Firecrawl website crawling for knowledge bases', TRUE, 'platform')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        "UPDATE super_admin.feature_flags SET rollout_percent = 5 WHERE code = 'chatbot.canary'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.broadcast_notifications")
    op.execute("DROP TABLE IF EXISTS super_admin.feature_flags")
    for code in ALL_PERMS:
        op.execute(f"DELETE FROM super_admin.permissions WHERE code = '{code}'")
