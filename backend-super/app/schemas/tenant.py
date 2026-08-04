from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from uuid import UUID


class OrgSummary(BaseModel):
    id: UUID
    name: str
    business_name: Optional[str] = None
    is_active: bool
    api_key_masked: str
    created_at: Optional[datetime] = None
    user_count: int = 0
    booking_count: int = 0
    conversation_count: int = 0
    session_retention_days: Optional[int] = None
    last_activity_at: Optional[datetime] = None


class OrgConfigSummary(BaseModel):
    timezone: Optional[str] = None
    require_otp: bool
    allow_same_day_booking: bool
    slot_cutoff_minutes: int
    max_booking_days_ahead: int
    session_retention_days: int
    meeting_mode: Optional[str] = None
    active_calendar_provider: Optional[str] = None
    active_meeting_provider: Optional[str] = None
    whatsapp_enabled: bool
    google_calendar_enabled: bool
    outlook_calendar_enabled: bool
    website_url: Optional[str] = None
    business_name: Optional[str] = None


class OrgDetail(OrgSummary):
    config: OrgConfigSummary


class UserSummary(BaseModel):
    id: UUID
    org_id: UUID
    org_name: str
    email: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class BookingSummary(BaseModel):
    id: UUID
    customer_name: str
    customer_email: str
    customer_phone: Optional[str] = None
    org_slot_start_at: Optional[datetime] = None
    org_slot_end_at: Optional[datetime] = None
    meeting_link_url: Optional[str] = None
    meeting_type_snapshot: Optional[str] = None
    created_at: Optional[datetime] = None


class SessionSummary(BaseModel):
    id: UUID
    channel: str
    session_status: str
    current_intent: Optional[str] = None
    human_agent_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    message_count: int = 0


class OrgListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[OrgSummary]


class UserListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[UserSummary]


class BookingListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[BookingSummary]


class SessionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SessionSummary]


class TrendPoint(BaseModel):
    date: str
    bookings: int
    conversations: int


class StatsResponse(BaseModel):
    orgs_total: int
    orgs_active: int
    orgs_suspended: int
    users_total: int
    users_active: int
    bookings_total: int
    bookings_today: int
    bookings_7d: int
    conversations_total: int
    conversations_active: int
    conversations_today: int
    alerts_open: int
    alerts_critical: int
    mrr: Optional[int] = None
    ai_tokens_30d: Optional[int] = None
    trend: list[TrendPoint]


class TopOrgRow(BaseModel):
    id: UUID
    name: str
    is_active: bool
    booking_count: int
    conversation_count: int


class TopOrgsResponse(BaseModel):
    items: list[TopOrgRow]


class UsageRow(BaseModel):
    metric: str
    count: int
    last_updated: Optional[datetime] = None


class UsageResponse(BaseModel):
    items: list[UsageRow]


class OrgAuditRow(BaseModel):
    id: UUID
    actor_email: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    old_values: Optional[dict] = None
    new_values: Optional[dict] = None
    created_at: Optional[datetime] = None


class OrgAuditResponse(BaseModel):
    total: int
    items: list[OrgAuditRow]


class UserProfile(UserSummary):
    booking_count: int = 0
    conversation_count: int = 0
    last_activity_at: Optional[datetime] = None
    recent_bookings: list[BookingSummary] = []


class UserActivityRow(BaseModel):
    id: UUID
    actor_email: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    created_at: Optional[datetime] = None


class UserActivityResponse(BaseModel):
    items: list[UserActivityRow]
