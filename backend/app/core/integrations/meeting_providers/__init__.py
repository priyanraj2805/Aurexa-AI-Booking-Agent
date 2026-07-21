"""
Meeting provider abstraction layer.

Provides a unified interface for generating online meeting links
(Google Meet, Microsoft Teams, Zoom) via the factory pattern.

This is SEPARATE from calendar sync — it handles meeting link generation only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.config import OrgConfig


class MeetingLinkErrorCode(str, Enum):
    """Error codes for meeting link generation failures."""

    NONE = "NONE"  # No error
    NOT_CONNECTED = "NOT_CONNECTED"  # Provider not connected for this org
    AUTH_EXPIRED = "AUTH_EXPIRED"  # OAuth tokens expired
    API_ERROR = "API_ERROR"  # Provider API returned error
    TIMEOUT = "TIMEOUT"  # Request timed out
    RATE_LIMITED = "RATE_LIMITED"  # Provider rate limit exceeded
    UNKNOWN = "UNKNOWN"  # Unknown error


@dataclass
class MeetingLinkResult:
    """
    Normalized result from meeting link generation.

    Structured status enables deterministic degraded handling —
    no exception-only control flow.
    """

    status: str  # "READY" | "UNAVAILABLE" | "FAILED"
    meeting_link_url: Optional[str] = None
    external_id: Optional[str] = None
    error_code: Optional[MeetingLinkErrorCode] = None
    error_message: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == "READY" and self.meeting_link_url is not None


class MeetingProvider(ABC):
    """Abstract base class for meeting provider integrations."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'google_meet', 'microsoft_teams', 'zoom')."""
        ...

    @abstractmethod
    async def is_connected(self, config: "OrgConfig") -> bool:
        """Check if this provider is connected for the organization."""
        ...

    @abstractmethod
    async def generate_meeting_link(
        self,
        config: "OrgConfig",
        db: "AsyncSession",
        booking_id: str,
        start_time: datetime,
        end_time: datetime,
        summary: str,
        attendee_email: Optional[str] = None,
    ) -> MeetingLinkResult:
        """
        Generate a meeting link for a booking.

        Returns MeetingLinkResult with status, URL, and error info.
        Never raises exceptions for expected failures — uses structured result instead.
        """
        ...
