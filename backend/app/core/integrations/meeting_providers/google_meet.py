"""
Google Meet provider implementation.

Uses Google Calendar API to create events with conferenceData for Meet links.
Token management reuses existing Google Calendar OAuth infrastructure.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional

import httpx

from app.core.integrations.meeting_providers import (
    MeetingProvider,
    MeetingLinkResult,
    MeetingLinkErrorCode,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.config import OrgConfig

logger = logging.getLogger(__name__)

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleMeetProvider(MeetingProvider):
    """Google Meet provider using Calendar API conferenceData."""

    provider_name = "google_meet"
    _http_client: Optional[httpx.AsyncClient] = None

    @classmethod
    async def get_http_client(cls) -> httpx.AsyncClient:
        if cls._http_client is None or cls._http_client.is_closed:
            cls._http_client = httpx.AsyncClient(timeout=30.0)
        return cls._http_client

    @classmethod
    async def close_http_client(cls) -> None:
        if cls._http_client and not cls._http_client.is_closed:
            await cls._http_client.aclose()
            cls._http_client = None

    async def is_connected(self, config: "OrgConfig") -> bool:
        """Check if Google Meet is connected via the calendar OAuth tokens."""
        return bool(
            config
            and config.google_meet_connected
            and config.google_calendar_refresh_token  # Reuses calendar OAuth
        )

    async def _refresh_token(self, refresh_token: str) -> Optional[str]:
        """Exchange refresh token for access token."""
        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            logger.error("Google OAuth credentials not configured")
            return None

        client = await self.get_http_client()
        try:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            tokens = resp.json()
            return tokens.get("access_token")
        except httpx.HTTPError as e:
            logger.error(f"Google token refresh failed: {e}")
            return None

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
        Generate a Google Meet link by creating a Calendar event with conferenceData.
        """
        if not config.google_calendar_refresh_token:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.AUTH_EXPIRED,
                error_message="No Google refresh token available",
            )

        access_token = await self._refresh_token(config.google_calendar_refresh_token)
        if not access_token:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.AUTH_EXPIRED,
                error_message="Failed to refresh Google access token",
            )

        # Create calendar event with conference request
        event = {
            "summary": summary,
            "description": f"Booking ID: {booking_id}",
            "start": {
                "dateTime": start_time.isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": end_time.isoformat(),
                "timeZone": "UTC",
            },
            "conferenceData": {
                "createRequest": {
                    "requestId": f"booking-{booking_id}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }

        if attendee_email:
            event["attendees"] = [{"email": attendee_email}]

        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{CALENDAR_API_BASE}/calendars/primary/events",
                params={"conferenceDataVersion": 1},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            resp.raise_for_status()
            event_data = resp.json()

            # Extract Meet link from conference data
            conference_data = event_data.get("conferenceData", {})
            entry_points = conference_data.get("entryPoints", [])
            meet_link = None
            for ep in entry_points:
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")
                    break

            if meet_link:
                return MeetingLinkResult(
                    status="READY",
                    meeting_link_url=meet_link,
                    external_id=event_data.get("id"),
                )
            else:
                return MeetingLinkResult(
                    status="UNAVAILABLE",
                    error_code=MeetingLinkErrorCode.API_ERROR,
                    error_message="No Meet link in event response",
                    external_id=event_data.get("id"),
                )

        except httpx.TimeoutException:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.TIMEOUT,
                error_message="Google Calendar API timeout",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return MeetingLinkResult(
                    status="UNAVAILABLE",
                    error_code=MeetingLinkErrorCode.RATE_LIMITED,
                    error_message="Google API rate limit exceeded",
                )
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message=f"Google API error: {e.response.status_code}",
            )
        except httpx.HTTPError as e:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message=str(e),
            )
