from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    PROJECT_NAME: str = "DotStark Super Admin Control Plane"
    API_V1_STR: str = "/api/v1"

    # Environment mode
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # ── Database (same PostgreSQL as the tenant backend) ──────────────────
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password")
    POSTGRES_SERVER: str = os.getenv("POSTGRES_SERVER", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "dotstark_v2")

    # ── Read-only role (tenant reads) ─────────────────────────────────────
    SUPER_ADMIN_READONLY_USER: str = os.getenv(
        "SUPER_ADMIN_READONLY_USER", "super_admin_readonly"
    )
    SUPER_ADMIN_READONLY_PASSWORD: str = os.getenv(
        "SUPER_ADMIN_READONLY_PASSWORD", "readonly_dev_pass_2026"
    )

    # ── Auth (Phase 1) ────────────────────────────────────────────────────
    # slowapi limit string for POST /auth/login. Default 10/minute; raise it
    # (e.g. 120/minute) only for automated E2E runs, never in production.
    LOGIN_RATE_LIMIT: str = os.getenv("LOGIN_RATE_LIMIT", "10/minute")

    SUPER_ADMIN_JWT_SECRET_KEY: str = os.getenv(
        "SUPER_ADMIN_JWT_SECRET_KEY",
        "dev-super-admin-secret-2026-change-in-prod",
    )
    SUPER_ADMIN_JWT_ALGORITHM: str = "HS256"
    SUPER_ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("SUPER_ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
    )
    SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS: int = int(
        os.getenv("SUPER_ADMIN_REFRESH_TOKEN_EXPIRE_DAYS", "7")
    )
    SUPER_ADMIN_COOKIE_NAME: str = "super_admin_token"
    SUPER_ADMIN_REFRESH_COOKIE_NAME: str = "super_admin_refresh"
    SUPER_ADMIN_STEPUP_COOKIE_NAME: str = "super_admin_stepup"

    # ── Env gates (stay false until GA cutover) ───────────────────────────
    INTERNAL_BRIDGE_ENABLED: bool = (
        os.getenv("INTERNAL_BRIDGE_ENABLED", "false").lower()
        in ("true", "1", "yes")
    )
    TELEMETRY_ENABLED: bool = (
        os.getenv("TELEMETRY_ENABLED", "false").lower() in ("true", "1", "yes")
    )

    # ── Internal bridge client (Phase 4) ──────────────────────────────────
    # URL of the MAIN tenant backend's bridge. In Docker Compose this is the
    # "backend" service; in local dev it is localhost:8005 (or the dedicated
    # bridge-enabled test instance on :8010).
    INTERNAL_API_URL: str = os.getenv("INTERNAL_API_URL", "http://localhost:8005")
    # Shared HMAC secret — MUST match the main backend's INTERNAL_BRIDGE_SECRET.
    INTERNAL_BRIDGE_SECRET: str = os.getenv(
        "INTERNAL_BRIDGE_SECRET", "dev_bridge_secret_2026"
    )
    INTERNAL_BRIDGE_MAX_SKEW_SECONDS: int = int(
        os.getenv("INTERNAL_BRIDGE_MAX_SKEW_SECONDS", "300")
    )

    # ── Billing (Phase 5) ─────────────────────────────────────────────────
    # Stripe is used only when a secret key is configured; otherwise the
    # deterministic MockProvider handles dev/test flows.
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    # Refunds at or below this amount (cents) execute with MFA step-up only;
    # larger refunds additionally require a second admin's approval.
    REFUND_APPROVAL_THRESHOLD_CENTS: int = int(
        os.getenv("REFUND_APPROVAL_THRESHOLD_CENTS", "50000")
    )
    # Step-up tokens (sensitive actions) validity in minutes.
    STEPUP_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("STEPUP_TOKEN_EXPIRE_MINUTES", "5")
    )

    # Frontend
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3003")

    # ── Org-admin consent OTP (Phase 13) ───────────────────────────────────
    # A short-lived numeric OTP emailed to the org admin (the email the
    # organization registers with in the admin panel) must be entered by the
    # super admin before an impersonation session can be confirmed or a
    # conversation transcript can be peeked. Fail-closed: when SMTP_HOST is
    # empty the endpoints return 502 and no OTP is ever exposed.
    OTP_TTL_MINUTES: int = int(os.getenv("OTP_TTL_MINUTES", "10"))
    OTP_MAX_ATTEMPTS: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
    OTP_RESEND_COOLDOWN_SECONDS: int = int(
        os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "60")
    )
    # Once an OTP is verified for an org, the grant stays valid this long so
    # the super admin can open several transcripts without re-OTP'ing.
    PEEK_GRANT_TTL_MINUTES: int = int(os.getenv("PEEK_GRANT_TTL_MINUTES", "30"))
    # ── Org-admin consent links (Phase 15, impersonation only) ─────────────
    # Instead of an OTP the org admin receives a confirmation link by email.
    # The link is valid 24 hours; clicking it shows a confirmation page and
    # the admin must click "I confirm" before the impersonation is approved.
    IMPERSONATION_APPROVAL_LINK_TTL_HOURS: int = int(
        os.getenv("IMPERSONATION_APPROVAL_LINK_TTL_HOURS", "24")
    )
    # Public base URL the emailed consent link points at. The confirmation
    # page is served by THIS backend (no super-admin login required there).
    IMPERSONATION_CONSENT_BASE_URL: str = os.getenv(
        "IMPERSONATION_CONSENT_BASE_URL", "http://localhost:8006"
    )
    # Tenant console (admin-panel) base URL. Used to build the "Open console"
    # handoff link after an impersonation session is confirmed.
    TENANT_CONSOLE_URL: str = os.getenv(
        "TENANT_CONSOLE_URL", "http://localhost:3002"
    )
    # Handshake ticket that hands the super admin into the tenant app as the
    # impersonated user. Short-lived and signed with the shared bridge secret
    # so the tenant backend can validate it without sharing JWTs.
    IMPERSONATION_HANDSHAKE_TTL_MINUTES: int = int(
        os.getenv("IMPERSONATION_HANDSHAKE_TTL_MINUTES", "10")
    )

    # ── Incident email notify (Phase 6, task 5) ───────────────────────────
    # When INCIDENT_NOTIFY_TO is empty, incident emails are skipped (no-op).
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "DotStark Ops <no-reply@dotstark.local>")
    # Comma-separated list of recipients for incident notifications.
    INCIDENT_NOTIFY_TO: str = os.getenv("INCIDENT_NOTIFY_TO", "")

    # ── Support tickets service key (Phase 12) ───────────────────────────
    # Shared secret the tenant backend presents (X-Support-Service-Key) when it
    # creates/lists tickets on behalf of an authenticated org admin. Must match
    # the tenant backend's SUPPORT_SERVICE_KEY.
    SUPPORT_SERVICE_KEY: str = os.getenv(
        "SUPPORT_SERVICE_KEY", "dev_support_service_key_2026"
    )

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """Admin pool — writes ONLY to the super_admin schema."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SUPER_ADMIN_READONLY_DATABASE_URI(self) -> str:
        """Read-only pool — SELECT-only access to tenant tables."""
        return (
            f"postgresql+asyncpg://{self.SUPER_ADMIN_READONLY_USER}:"
            f"{self.SUPER_ADMIN_READONLY_PASSWORD}@{self.POSTGRES_SERVER}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
