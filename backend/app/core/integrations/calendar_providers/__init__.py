"""
Calendar provider abstraction layer.

Provides a unified interface for syncing bookings to calendar services
(Google Calendar, Microsoft Outlook) via the factory pattern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.config import OrgConfig


class CalendarProvider(ABC):
    """Abstract base class for calendar provider integrations."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'google', 'outlook')."""
        ...

    @abstractmethod
    async def is_enabled(self, config: OrgConfig) -> bool: ...

    @abstractmethod
    async def is_connected(self, config: OrgConfig) -> bool: ...

    @abstractmethod
    def get_auth_url(self, state: str) -> str:
        """Return the OAuth authorization URL for this provider."""
        ...

    @abstractmethod
    async def handle_callback(
        self, code: str, config: OrgConfig, db: AsyncSession
    ) -> bool:
        """Exchange OAuth code for tokens and persist to config."""
        ...

    @abstractmethod
    async def disconnect(self, config: OrgConfig, db: AsyncSession) -> None:
        """Remove stored tokens from config."""
        ...

    @abstractmethod
    async def get_busy_periods(
        self,
        config: OrgConfig,
        start_time: datetime,
        end_time: datetime,
        db: Optional[AsyncSession] = None,
    ) -> List[Tuple[datetime, datetime]]:
        """
        Fetch busy periods for conflict detection.
        Returns list of (start, end) datetime tuples representing busy times.
        """
        ...

    @abstractmethod
    async def create_event(
        self,
        config: OrgConfig,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
        customer_email: str,
        customer_phone: Optional[str],
        source_channel: str,
        booking_id: str,
        db: AsyncSession,
    ) -> Optional[str]:
        """
        Create a calendar event for a booking.
        Returns the provider's event ID on success, None on failure.
        """
        ...

    @abstractmethod
    async def update_event(
        self,
        config: OrgConfig,
        event_id: str,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
        db: AsyncSession,
    ) -> bool:
        """Update a calendar event. Returns True on success."""
        ...

    @abstractmethod
    async def delete_event(
        self, config: OrgConfig, event_id: str, db: AsyncSession
    ) -> bool:
        """Delete a calendar event. Returns True on success."""
        ...

    @abstractmethod
    async def get_events(
        self,
        config: OrgConfig,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        db: Optional[AsyncSession] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch events from the provider's calendar."""
        ...
