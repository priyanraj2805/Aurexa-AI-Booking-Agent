from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EmailDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"


class EmailChannel(str, Enum):
    OTP = "otp"
    BOOKING_CONFIRMATION = "booking_confirmation"
    BOOKING_CANCELLATION = "booking_cancellation"


class EmailDeliveryCreate(BaseModel):
    org_id: UUID
    channel: EmailChannel
    recipient_email: str
    subject: str
    provider: str = "primary"
    request_id: Optional[str] = None
    max_attempts: int = 3
    payload: Optional[dict] = None


class EmailDeliveryResponse(BaseModel):
    id: UUID
    org_id: UUID
    channel: EmailChannel
    recipient_email: str
    subject: str
    provider: str
    status: EmailDeliveryStatus
    attempt_count: int
    max_attempts: int
    next_retry_at: Optional[datetime] = None
    last_error: Optional[str] = None
    request_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmailHistoryFilter(BaseModel):
    status: Optional[EmailDeliveryStatus] = None
    channel: Optional[EmailChannel] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class EmailHistoryResponse(BaseModel):
    id: UUID
    recipient_email: str
    status: EmailDeliveryStatus
    attempt_count: int
    provider: str
    last_error: Optional[str] = None
    created_at: datetime
    channel: EmailChannel
    request_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

