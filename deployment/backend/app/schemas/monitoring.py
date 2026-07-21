from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AlertComponent(str, Enum):
    LLM = "llm"
    EMAIL = "email"
    DATABASE = "database"
    BOOKING = "booking"
    TIMEZONE = "timezone"


class AlertSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class SystemAlertCreate(BaseModel):
    org_id: Optional[UUID] = None
    component: AlertComponent
    severity: AlertSeverity
    code: str
    message: str
    dedupe_key: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    context: Optional[dict] = None


class SystemAlertResponse(BaseModel):
    id: UUID
    org_id: Optional[UUID] = None
    component: AlertComponent
    severity: AlertSeverity
    code: str
    dedupe_key: Optional[str] = None
    message: str
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    occurrence_count: int = 1
    context: Optional[dict] = None
    created_at: datetime
    last_seen_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    acknowledged_by_user_id: Optional[UUID] = None
    acknowledgement_note: Optional[str] = None
    resolved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class EscalationRoutingTargetPayload(BaseModel):
    target_type: str = Field(min_length=1, max_length=32)
    target_value: str = Field(min_length=1, max_length=255)


class EscalationRotationWindowPayload(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_hour_utc: int = Field(ge=0, le=23)
    end_hour_utc: int = Field(ge=0, le=23)


class EscalationPolicyUpdateRequest(BaseModel):
    routing_targets: list[EscalationRoutingTargetPayload] = Field(default_factory=list)
    rotation_windows: list[EscalationRotationWindowPayload] = Field(
        default_factory=list
    )


class EscalationRoutingTargetResponse(EscalationRoutingTargetPayload):
    id: UUID
    order_index: int


class EscalationRotationWindowResponse(EscalationRotationWindowPayload):
    id: UUID
    order_index: int


class EscalationPolicyResponse(BaseModel):
    id: UUID
    org_id: UUID
    routing_targets: list[EscalationRoutingTargetResponse] = Field(default_factory=list)
    rotation_windows: list[EscalationRotationWindowResponse] = Field(
        default_factory=list
    )
    created_at: datetime
    updated_at: datetime


class AcknowledgeSystemAlertRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class AlertTrendAnomaly(BaseModel):
    is_anomaly: bool
    score: float = 0.0
    reason: Optional[str] = None


class AlertTrendBucket(BaseModel):
    bucket_start: datetime
    total_alerts: int
    critical_alerts: int
    unresolved_alerts: int
    anomaly: AlertTrendAnomaly


class AlertTrendSummary(BaseModel):
    latest_total: int
    latest_critical: int
    latest_unresolved: int
    baseline_avg_total: float
    anomaly_buckets_count: int


class AlertTrendResponse(BaseModel):
    window_hours: int
    bucket_minutes: int
    series: list[AlertTrendBucket]
    summary: AlertTrendSummary


class SystemAlertFilter(BaseModel):
    severity: Optional[AlertSeverity] = None
    component: Optional[AlertComponent] = None
    unresolved_only: bool = False
    recent_hours: Optional[int] = Field(default=None, ge=1, le=720)
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
