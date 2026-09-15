from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class DirectiveType(str, Enum):
    SELECT_DATE = "SELECT_DATE"
    SELECT_TIME = "SELECT_TIME"
    SELECT_SLOT = "SELECT_SLOT"
    CONFIRM_BOOKING = "CONFIRM_BOOKING"
    CHANGE_SELECTION = "CHANGE_SELECTION"
    CANCEL_BOOKING = "CANCEL_BOOKING"


class DirectiveStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class DirectiveOption(BaseModel):
    id: str
    label: str
    value: str


class BookingDirectiveCreate(BaseModel):
    type: DirectiveType
    title: str
    description: str = ""
    options: List[DirectiveOption] = []
    required: bool = True
    expires_at: Optional[datetime] = None
    meta: dict = {}


class BookingDirectiveResponse(BaseModel):
    directive_id: UUID
    booking_id: Optional[UUID] = None
    org_id: UUID
    customer_id: str
    session_id: UUID
    type: DirectiveType
    status: DirectiveStatus
    title: str
    description: Optional[str] = None
    options: List[DirectiveOption]
    required: bool
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    selected_option: Optional[DirectiveOption] = None
    meta: dict = {}
    created_at: datetime

    class Config:
        from_attributes = True
