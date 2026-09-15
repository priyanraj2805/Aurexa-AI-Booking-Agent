import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "DotStark AI Booking Assistant Enterprise V2"
    API_V1_STR: str = "/api/v1"

    # Frontend URL
    FRONTEND_URL: str = os.getenv("FRONTEND_URL") or os.getenv(
        "FRONTEND_BASE_URL", "http://localhost:3002"
    )

    # Environment mode
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # POSTGRESQL
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password")
    POSTGRES_SERVER: str = os.getenv("POSTGRES_SERVER", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "dotstark_v2")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    DB_SLOW_QUERY_THRESHOLD: int = int(os.getenv("DB_SLOW_QUERY_THRESHOLD", "1000"))

    # SMTP (for OTP and Confirmations)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMAILS_FROM_EMAIL: str = os.getenv("EMAILS_FROM_EMAIL", SMTP_USER)

    # Secondary SMTP provider (EMAIL-03 fallback)
    SMTP_SECONDARY_HOST: str = os.getenv("SMTP_SECONDARY_HOST", "")
    SMTP_SECONDARY_PORT: int = int(os.getenv("SMTP_SECONDARY_PORT", "587"))
    SMTP_SECONDARY_USER: str = os.getenv("SMTP_SECONDARY_USER", "")
    SMTP_SECONDARY_PASSWORD: str = os.getenv("SMTP_SECONDARY_PASSWORD", "")
    SMTP_SECONDARY_FROM_EMAIL: str = os.getenv(
        "SMTP_SECONDARY_FROM_EMAIL", SMTP_SECONDARY_USER
    )

    # Email reliability knobs
    EMAIL_RETRY_MAX_ATTEMPTS: int = int(os.getenv("EMAIL_RETRY_MAX_ATTEMPTS", "3"))

    # LLM
    LLM_BASE_URL: str = os.getenv(
        "LLM_BASE_URL", "https://api.groq.com/openai/v1"
    )
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "moonshotai/kimi-k2-instruct-0905")

    # Fallback LLM — used when the primary provider fails (rate limit,
    # timeout, 5xx). Empty values disable the fallback entirely.
    LLM_FALLBACK_BASE_URL: str = os.getenv("LLM_FALLBACK_BASE_URL", "")
    LLM_FALLBACK_API_KEY: str = os.getenv("LLM_FALLBACK_API_KEY", "")
    LLM_FALLBACK_MODEL: str = os.getenv("LLM_FALLBACK_MODEL", "")

    # Firecrawl (for website crawling)
    FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")

    

    # Firebase Phone Auth (aurexa / dotstark-833ec)
    FIREBASE_API_KEY: str = os.getenv("FIREBASE_API_KEY", "AIzaSyC7zH8cEITPDpfyiqhPo7ZlK1-2DViL1BI")
    FIREBASE_AUTH_DOMAIN: str = os.getenv("FIREBASE_AUTH_DOMAIN", "dotstark-833ec.firebaseapp.com")
    FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "dotstark-833ec")
    FIREBASE_APP_ID: str = os.getenv("FIREBASE_APP_ID", "1:287417969813:web:7ba50454917fe54d4f3f9c")

    # NOTE: Meta Cloud API WhatsApp removed (QR/Baileys transport is the only
    # WhatsApp channel now). OTP delivery keeps its own WHATSAPP_OTP_* settings.

    # Zavu SMS (phone OTP via Zavu shared/one-way sender)
    ZAVU_API_KEY: str = os.getenv("ZAVU_API_KEY", "")
    ZAVU_SENDER_ID: str = os.getenv("ZAVU_SENDER_ID", "kd78m7agx48rpyegpfrmhp5rsx8dm8t9")
    ZAVU_BASE_URL: str = os.getenv("ZAVU_BASE_URL", "https://api.zavu.dev")

    # Phase 2 runtime cutover controls (web channel only)
    RUNTIME_CANARY_FALLBACK_ENABLED: bool = (
        os.getenv("RUNTIME_CANARY_FALLBACK_ENABLED", "false").lower()
        in ("true", "1", "yes")
    )
    RUNTIME_FALLBACK_SUNSET_ENFORCED: bool = (
        os.getenv("RUNTIME_FALLBACK_SUNSET_ENFORCED", "false").lower()
        in ("true", "1", "yes")
    )

    # ── Super Admin internal bridge (Phase 2) ─────────────────────────────
    # When disabled (default), the bridge router is NOT registered and every
    # bridge call returns 404. Enable only for the GA cutover, or in a
    # dedicated sidecar instance for testing.
    INTERNAL_BRIDGE_ENABLED: bool = (
        os.getenv("INTERNAL_BRIDGE_ENABLED", "false").lower()
        in ("true", "1", "yes")
    )
    INTERNAL_BRIDGE_SECRET: str = os.getenv(
        "INTERNAL_BRIDGE_SECRET", ""
    )
    # Timestamp window (seconds) for bridge HMAC replay protection.
    INTERNAL_BRIDGE_MAX_SKEW_SECONDS: int = int(
        os.getenv("INTERNAL_BRIDGE_MAX_SKEW_SECONDS", "300")
    )

    # ── Telemetry (Phase 2) ───────────────────────────────────────────────
    # Writes usage_events counters (super_admin schema). Default OFF.
    TELEMETRY_ENABLED: bool = (
        os.getenv("TELEMETRY_ENABLED", "false").lower() in ("true", "1", "yes")
    )

    # ── Support tickets forwarding (Phase 12) ─────────────────────────────
    # URL of the backend-super control plane. The tenant backend forwards org
    # admin support requests there (service-key-auth'd), so tickets live in the
    # control plane and the super admin team owns a single queue.
    SUPER_ADMIN_API_URL: str = os.getenv("SUPER_ADMIN_API_URL", "http://localhost:8006")
    SUPPORT_SERVICE_KEY: str = os.getenv(
        "SUPPORT_SERVICE_KEY", "dev_support_service_key_2026"
    )

    # ── Razorpay (platform account for Route payouts) ────────────────────
    # Booking payments are created on the platform account and split to
    # providers via Razorpay Route. No per-org keys, no OAuth tokens.
    RAZORPAY_PARTNER_MODE: str = os.getenv("RAZORPAY_PARTNER_MODE", "test")  # test|live
    RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    # Default appointment price in paise (e.g., 50000 = ₹500). 0 = free/no payment.
    RAZORPAY_DEFAULT_AMOUNT_PAISE: int = int(os.getenv("RAZORPAY_DEFAULT_AMOUNT_PAISE", "0"))

    # ── Razorpay Route (provider payouts, additive to legacy sub-merchant flow) ──
    # Kill switch: when false, all Route endpoints return 404 and no Route API
    # call is made. Data is preserved. TEST mode only for initial rollout.
    # Platform credentials are the existing RAZORPAY_KEY_ID/SECRET (rzp_*);
    # Route MUST NOT read per-org OAuth tokens from OrgConfig.
    RAZORPAY_ROUTE_ENABLED: bool = os.getenv("RAZORPAY_ROUTE_ENABLED", "false").lower() in ("true", "1", "yes")
    RAZORPAY_ROUTE_API_BASE: str = os.getenv("RAZORPAY_ROUTE_API_BASE", "https://api.razorpay.com")
    # Default platform commission in basis points (1000 = 10%). Integer math only.
    # Per-transfer value is persisted; this is only the fallback default.
    RAZORPAY_ROUTE_COMMISSION_BPS: int = int(os.getenv("RAZORPAY_ROUTE_COMMISSION_BPS", "1000"))

    # ── OTP Phone Provider (SMS ↔ WhatsApp) ──────────────────────────────
    # whatsapp = active (Meta Cloud API), sms = Zavu sms_oneway fallback
    OTP_PHONE_PROVIDER: str = os.getenv("OTP_PHONE_PROVIDER", "whatsapp")  # whatsapp | sms
    WHATSAPP_OTP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_OTP_PHONE_NUMBER_ID", "")
    WHATSAPP_OTP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_OTP_ACCESS_TOKEN", "")
    WHATSAPP_OTP_API_VERSION: str = os.getenv("WHATSAPP_OTP_API_VERSION", "v20.0")
    WHATSAPP_OTP_TEMPLATE_NAME: str = os.getenv("WHATSAPP_OTP_TEMPLATE_NAME", "")  # empty = plain text for test
    WHATSAPP_OTP_LANGUAGE: str = os.getenv("WHATSAPP_OTP_LANGUAGE", "en_US")

    # ── Meta WhatsApp Cloud API webhook (receive-only foundation) ────────
    # WABA phone number id this webhook belongs to (e.g. 1288066011058334).
    # Compared against metadata.phone_number_id; mismatches are logged + skipped.
    META_WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("META_WHATSAPP_PHONE_NUMBER_ID", "")
    # Webhook verification token for Meta's GET handshake (hub.verify_token).
    # Never the Meta access token. Fail-closed when empty.
    META_WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = os.getenv("META_WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")

    # ── WhatsApp QR gateway (Baileys transport, per-org sessions) ─────────
    # Shared secret for backend<->wa-gateway HTTP auth (both sides must match).
    WA_GATEWAY_SERVICE_KEY: str = os.getenv("WA_GATEWAY_SERVICE_KEY", "")
    # Gateway base URL as seen from the backend container.
    WA_GATEWAY_BASE_URL: str = os.getenv("WA_GATEWAY_BASE_URL", "http://wa-gateway:3100")

    def validate_production_razorpay_config(self) -> list[str]:
        """Return list of config errors when MODE=live. Does not log secrets."""
        errors: list[str] = []
        if (self.RAZORPAY_PARTNER_MODE or "test").lower() == "live":
            if not self.RAZORPAY_KEY_ID or not str(self.RAZORPAY_KEY_ID).startswith("rzp_"):
                errors.append("RAZORPAY_KEY_ID must be a platform rzp_* key in live mode")
            if not self.RAZORPAY_KEY_SECRET:
                errors.append("RAZORPAY_KEY_SECRET is required in live mode")
            if not self.RAZORPAY_WEBHOOK_SECRET:
                errors.append("RAZORPAY_WEBHOOK_SECRET is required in live mode")
            if self.FRONTEND_URL and ("localhost" in self.FRONTEND_URL or self.FRONTEND_URL.startswith("http://")):
                # Only warn if frontend is still localhost in live mode — indicates misconfiguration
                errors.append("FRONTEND_URL should be https production domain in live mode (affects webhook URLs)")
            if self.RAZORPAY_ROUTE_ENABLED:
                if not (0 <= (self.RAZORPAY_ROUTE_COMMISSION_BPS or 0) <= 10000):
                    errors.append("RAZORPAY_ROUTE_COMMISSION_BPS must be between 0 and 10000")
        return errors

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"


    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        # Render and other providers often provide DATABASE_URL
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            # asyncpg requires postgresql+asyncpg:// instead of postgres://
            if db_url.startswith("postgres://"):
                db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
            elif db_url.startswith("postgresql://"):
                db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return db_url

        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"


settings = Settings()
