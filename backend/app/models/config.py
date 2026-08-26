import uuid
from enum import Enum
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    ForeignKey,
    LargeBinary,
    DateTime,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db.base import Base


class CalendarProviderType(str, Enum):
    NONE = "NONE"
    GOOGLE = "GOOGLE"
    OUTLOOK = "OUTLOOK"


class MeetingMode(str, Enum):
    OFFLINE_ONLY = "OFFLINE_ONLY"
    ONLINE_ONLY = "ONLINE_ONLY"
    HYBRID = "HYBRID"


class MeetingProviderDefault(str, Enum):
    NONE = "NONE"
    GOOGLE_MEET = "GOOGLE_MEET"
    MICROSOFT_TEAMS = "MICROSOFT_TEAMS"
    ZOOM = "ZOOM"


class ActiveMeetingProvider(str, Enum):
    """Active meeting provider for generating meeting links (distinct from calendar sync)."""

    NONE = "NONE"
    GOOGLE_MEET = "GOOGLE_MEET"
    MICROSOFT_TEAMS = "MICROSOFT_TEAMS"
    ZOOM = "ZOOM"


class MeetingLinkStatus(str, Enum):
    """Status of meeting link generation for a booking."""

    NOT_REQUIRED = "NOT_REQUIRED"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class OrgConfig(Base):
    __tablename__ = "org_configs"

    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    required_fields = Column(JSONB, nullable=False)
    field_definitions = Column(JSONB, nullable=True)
    custom_prompts = Column(JSONB, nullable=True)
    timezone = Column(String(100), default="UTC")
    allow_same_day_booking = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    slot_cutoff_minutes = Column(
        Integer, default=5, server_default="5", nullable=False
    )
    ui_brand_color = Column(String(10), default="#3B82F6")
    require_otp = Column(Boolean, default=True, server_default="true", nullable=False)
    max_booking_days_ahead = Column(
        Integer, default=90, server_default="90", nullable=False
    )
    session_retention_days = Column(
        Integer, default=30, server_default="30", nullable=False
    )
    session_cleanup_frequency = Column(
        String(20), default="daily", server_default="daily", nullable=False
    )
    session_cleanup_hour_utc = Column(
        Integer, default=0, server_default="0", nullable=False
    )

    # WhatsApp integration
    whatsapp_enabled = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    whatsapp_phone_number_id = Column(String(255), nullable=True)
    whatsapp_business_account_id = Column(String(255), nullable=True)
    whatsapp_verify_token = Column(String(255), nullable=True)
    whatsapp_api_token = Column(String(512), nullable=True)

    # Google Calendar integration
    google_calendar_enabled = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    google_calendar_connected = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    google_calendar_refresh_token = Column(String(512), nullable=True)

    # Microsoft Outlook Calendar integration
    outlook_calendar_enabled = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    outlook_calendar_connected = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    outlook_calendar_refresh_token = Column(String(length=1024), nullable=True)
    outlook_calendar_access_token = Column(String(length=4096), nullable=True)
    outlook_calendar_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Active calendar provider (single-active architecture)
    active_calendar_provider = Column(
        SQLEnum(CalendarProviderType, native_enum=False, length=20),
        default=CalendarProviderType.NONE,
        server_default="NONE",
        nullable=False,
    )
    meeting_mode = Column(
        SQLEnum(MeetingMode, native_enum=False, length=20),
        default=MeetingMode.OFFLINE_ONLY,
        server_default="OFFLINE_ONLY",
        nullable=False,
    )
    allow_hybrid_selection = Column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    meeting_provider_default = Column(
        SQLEnum(MeetingProviderDefault, native_enum=False, length=32),
        default=MeetingProviderDefault.NONE,
        server_default="NONE",
        nullable=False,
    )
    meeting_policy_updated_at = Column(DateTime(timezone=True), nullable=True)
    meeting_policy_updated_by = Column(String(255), nullable=True)

    # Meeting provider connection state (separate from calendar sync)
    google_meet_connected = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    microsoft_teams_connected = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    zoom_connected = Column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Zoom OAuth tokens
    zoom_refresh_token = Column(String(512), nullable=True)
    zoom_access_token = Column(String(2048), nullable=True)
    zoom_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Active meeting provider (distinct from active_calendar_provider)
    active_meeting_provider = Column(
        SQLEnum(ActiveMeetingProvider, native_enum=False, length=32),
        default=ActiveMeetingProvider.NONE,
        server_default="NONE",
        nullable=False,
    )

    # Meeting provider audit trail
    meeting_provider_updated_at = Column(DateTime(timezone=True), nullable=True)
    meeting_provider_updated_by = Column(String(255), nullable=True)

    # Widget token (separate from API key for secure embed)
    widget_token = Column(String(64), nullable=True)

    # Business name — used for RAG filtering (separate from legal org name)
    business_name = Column(String(255), nullable=True)

    # Vectorless RAG data sources (PDFs + crawled website content).
    # These columns back the admin "Agent Personalization" panel and are
    # populated by uploads/ crawls. Chunk text itself lives in document_chunks.
    website_url = Column(String(2048), nullable=True)
    business_name = Column(String(255), nullable=True)
    crawl_id = Column(String(128), nullable=True)
    crawl_status = Column(String(32), nullable=True)
    crawl_started_at = Column(DateTime(timezone=True), nullable=True)
    crawl_completed_at = Column(DateTime(timezone=True), nullable=True)
    total_pages_crawled = Column(Integer, nullable=True)
    uploaded_docs = Column(JSONB, nullable=True)
    org_name = Column(String(255), nullable=True)

    # Structured context collected from the admin before knowledge ingestion:
    # {"business_profile": {...}, "sources": {"<doc_id|website>": {...}}}
    # Injected into the agent system prompt and stamped onto chunk metadata.
    knowledge_context = Column(JSONB, nullable=True)
