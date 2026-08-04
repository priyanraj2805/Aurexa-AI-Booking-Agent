"""superadmin_phase0_foundations

Creates the Super Admin control plane foundations:
  - `super_admin` schema (roles, permissions, role_permissions,
    super_admins, admin_mfa, admin_sessions, login_attempts)
  - `super_admin_readonly` DB role: SELECT-only access to the tenant
    `public` schema (used by backend-super for read queries)
  - Seed data: 26 permissions, 5 roles, role→permission mapping,
    bootstrap super admin

Also merges the two existing migration heads
(add_business_name_column, add_org_name_column).

Revision ID: superadmin_phase0_foundations
Revises: add_business_name_column, add_org_name_column
Create Date: 2026-08-03

"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from passlib.context import CryptContext

# revision identifiers, used by Alembic.
revision: str = "superadmin_phase0_foundations"
down_revision: Union[str, Sequence[str], None] = (
    "add_business_name_column",
    "add_org_name_column",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── Seed data ─────────────────────────────────────────────────────────────
# (code, module, name, description)
PERMISSIONS = [
    ("dashboard.view", "dashboard", "View Dashboard", "View platform KPIs and dashboard widgets"),
    ("orgs.view", "organizations", "View Organizations", "List and view organization details"),
    ("orgs.create", "organizations", "Create Organizations", "Onboard new customer organizations"),
    ("orgs.update", "organizations", "Update Organizations", "Edit organization configuration"),
    ("orgs.suspend", "organizations", "Suspend Organizations", "Block all access for an organization"),
    ("orgs.restore", "organizations", "Restore Organizations", "Reactivate a suspended organization"),
    ("orgs.export", "organizations", "Export Organization Data", "GDPR data export for an organization"),
    ("orgs.delete", "organizations", "Delete Organizations", "Permanent deletion (2-person approval)"),
    ("users.view", "users", "View Users", "Search and view users across organizations"),
    ("users.disable", "users", "Disable Users", "Block a user from logging in"),
    ("users.enable", "users", "Enable Users", "Restore a disabled user"),
    ("users.reset_password", "users", "Reset User Passwords", "Trigger OTP password reset for a user"),
    ("plans.view", "plans", "View Plans", "View subscription plans and limits"),
    ("plans.manage", "plans", "Manage Plans", "Create, edit and archive plans"),
    ("plans.assign", "plans", "Assign Plans", "Assign plans and change organization plans"),
    ("billing.view", "billing", "View Billing", "View invoices and billing history"),
    ("billing.retry_payment", "billing", "Retry Payments", "Retry failed invoice payments"),
    ("billing.refund", "billing", "Issue Refunds", "Issue refunds (threshold + MFA step-up)"),
    ("analytics.view", "analytics", "View Analytics", "View the 12 analytics report types"),
    ("analytics.export", "analytics", "Export Analytics", "Export analytics reports as CSV"),
    ("monitoring.view", "monitoring", "View Monitoring", "View dependency health and status"),
    ("alerts.manage", "monitoring", "Manage Alerts", "Acknowledge and resolve alerts"),
    ("incidents.manage", "monitoring", "Manage Incidents", "Create, notify and resolve incidents"),
    ("ops.manage", "operations", "Manage Ops Tools", "Feature flags, canary rollout, kill switch, broadcasts"),
    ("support.impersonate", "support", "Impersonate Users", "Act as an organization admin (audited, 30-min)"),
    ("audit.view", "audit", "View Audit Logs", "Read the tamper-proof audit trail"),
]

# role_code -> list of permission codes
ROLE_PERMISSIONS = {
    "owner": [p[0] for p in PERMISSIONS],
    "super_admin": [p[0] for p in PERMISSIONS if p[0] not in ("orgs.delete", "billing.refund")],
    "operations": [
        "dashboard.view",
        "orgs.view", "orgs.create", "orgs.update", "orgs.suspend", "orgs.restore", "orgs.export",
        "users.view", "users.disable", "users.enable", "users.reset_password",
        "plans.view", "plans.manage", "plans.assign",
        "billing.view", "billing.retry_payment",
        "analytics.view", "analytics.export",
        "monitoring.view", "alerts.manage", "incidents.manage",
        "ops.manage", "audit.view",
    ],
    "support": [
        "dashboard.view", "orgs.view", "users.view", "billing.view",
        "analytics.view", "monitoring.view", "support.impersonate", "audit.view",
    ],
    "auditor": [
        "dashboard.view", "orgs.view", "users.view", "billing.view",
        "analytics.view", "audit.view",
    ],
}

ROLES = {
    "owner": "Owner",
    "super_admin": "Super Admin",
    "operations": "Operations",
    "support": "Support",
    "auditor": "Auditor",
}

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _readonly_password() -> str:
    return os.getenv("SUPER_ADMIN_READONLY_PASSWORD", "readonly_dev_pass_2026")


def _bootstrap_password_hash() -> str:
    pw = os.getenv("SUPER_ADMIN_BOOTSTRAP_PASSWORD", "SuperAdmin@2026!")
    return _pwd_ctx.hash(pw)


def _bootstrap_email() -> str:
    return os.getenv("SUPER_ADMIN_BOOTSTRAP_EMAIL", "admin@dotstark.ai")


def _db_name() -> str:
    return os.getenv("POSTGRES_DB", "dotstark_v2")


def upgrade() -> None:
    # ── 1. super_admin schema ──────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS super_admin")

    # ── 2. Roles table ─────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.roles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            is_system BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ── 3. Permissions table ───────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.permissions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(100) NOT NULL UNIQUE,
            module VARCHAR(50) NOT NULL,
            name VARCHAR(100) NOT NULL,
            description TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_permissions_module "
        "ON super_admin.permissions (module)"
    )

    # ── 4. Role → permission mapping ───────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.role_permissions (
            role_id UUID NOT NULL REFERENCES super_admin.roles (id) ON DELETE CASCADE,
            permission_id UUID NOT NULL REFERENCES super_admin.permissions (id) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_id)
        )
        """
    )

    # ── 5. Super admin accounts ────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.super_admins (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            full_name VARCHAR(150),
            role_id UUID NOT NULL REFERENCES super_admin.roles (id),
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            mfa_required BOOLEAN NOT NULL DEFAULT TRUE,
            last_login_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_super_admins_role_id "
        "ON super_admin.super_admins (role_id)"
    )

    # ── 6. MFA records ─────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.admin_mfa (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            admin_id UUID NOT NULL UNIQUE REFERENCES super_admin.super_admins (id) ON DELETE CASCADE,
            totp_secret VARCHAR(64) NOT NULL,
            is_verified BOOLEAN NOT NULL DEFAULT FALSE,
            verified_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ── 7. Sessions (refresh tokens) ───────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.admin_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            admin_id UUID NOT NULL REFERENCES super_admin.super_admins (id) ON DELETE CASCADE,
            refresh_token_hash VARCHAR(255) NOT NULL,
            ip_address VARCHAR(45),
            user_agent TEXT,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_sessions_admin_id "
        "ON super_admin.admin_sessions (admin_id)"
    )

    # ── 8. Login attempts (brute-force lockout) ────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS super_admin.login_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) NOT NULL,
            admin_id UUID REFERENCES super_admin.super_admins (id) ON DELETE SET NULL,
            ip_address VARCHAR(45),
            success BOOLEAN NOT NULL DEFAULT FALSE,
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_login_attempts_email "
        "ON super_admin.login_attempts (email, attempted_at)"
    )

    # ── 9. Read-only DB role for tenant data ───────────────────────────────
    readonly_pw = _readonly_password().replace("'", "''")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'super_admin_readonly') THEN "
        f"CREATE ROLE super_admin_readonly LOGIN PASSWORD '{readonly_pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE; "
        "END IF; "
        "END $$;"
    )
    op.execute(f"GRANT CONNECT ON DATABASE {_db_name()} TO super_admin_readonly")
    op.execute("GRANT USAGE ON SCHEMA public TO super_admin_readonly")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO super_admin_readonly")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT ON TABLES TO super_admin_readonly"
    )
    # Belt and braces: even if a future GRANT is wrong, writes fail at
    # the transaction level.
    op.execute("ALTER ROLE super_admin_readonly SET default_transaction_read_only = on")

    # ── 10. Seed permissions ───────────────────────────────────────────────
    for code, module, name, desc in PERMISSIONS:
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.permissions (id, code, module, name, description)
                VALUES (gen_random_uuid(), :code, :module, :name, :desc)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "module": module, "name": name, "desc": desc},
        )

    # ── 11. Seed roles ─────────────────────────────────────────────────────
    for code, name in ROLES.items():
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO super_admin.roles (id, code, name, description)
                VALUES (gen_random_uuid(), :code, :name, :desc)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "name": name, "desc": f"System role: {name}"},
        )

    # ── 12. Seed role → permission mapping ─────────────────────────────────
    for role_code, codes in ROLE_PERMISSIONS.items():
        for perm_code in codes:
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
                {"rc": role_code, "pc": perm_code},
            )

    # ── 13. Seed bootstrap super admin (owner role, MFA forced) ───────────
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO super_admin.super_admins (id, email, password_hash, full_name, role_id, status, mfa_required, created_at, updated_at)
            SELECT gen_random_uuid(), :email, :pwd_hash, 'Bootstrap Admin', id, 'active', TRUE, now(), now()
            FROM super_admin.roles
            WHERE code = 'owner'
            ON CONFLICT (email) DO NOTHING
            """
        ),
        {"email": _bootstrap_email(), "pwd_hash": _bootstrap_password_hash()},
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS super_admin.login_attempts")
    op.execute("DROP TABLE IF EXISTS super_admin.admin_sessions")
    op.execute("DROP TABLE IF EXISTS super_admin.admin_mfa")
    op.execute("DROP TABLE IF EXISTS super_admin.super_admins")
    op.execute("DROP TABLE IF EXISTS super_admin.role_permissions")
    op.execute("DROP TABLE IF EXISTS super_admin.permissions")
    op.execute("DROP TABLE IF EXISTS super_admin.roles")
    op.execute("DROP ROLE IF EXISTS super_admin_readonly")
    op.execute("DROP SCHEMA IF EXISTS super_admin")
