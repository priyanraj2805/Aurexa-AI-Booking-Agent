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

    # Firecrawl (for website crawling)
    FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")

    # WhatsApp
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")

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
