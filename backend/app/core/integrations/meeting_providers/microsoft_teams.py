"""
Microsoft Teams provider implementation.

Uses Microsoft Graph API to create online meetings.
Token management reuses existing Outlook Calendar OAuth infrastructure.
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

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
MS_AUTH_BASE = "https://login.microsoftonline.com"

# Teams-specific scope for online meetings
TEAMS_SCOPES = ["OnlineMeetings.ReadWrite", "Calendars.ReadWrite"]


class MicrosoftTeamsProvider(MeetingProvider):
    """Microsoft Teams provider using Graph API online meetings."""

    provider_name = "microsoft_teams"
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
        """Check if Microsoft Teams is connected."""
        return bool(
            config
            and config.microsoft_teams_connected
            and config.outlook_calendar_refresh_token  # Reuses Outlook OAuth
        )

    async def _get_valid_access_token(
        self, config: "OrgConfig", db: "AsyncSession"
    ) -> Optional[str]:
        """Get valid access token, refreshing if needed."""
        # Check if current token is valid
        if config.outlook_calendar_access_token:
            if config.outlook_calendar_token_expires_at:
                exp_time = config.outlook_calendar_token_expires_at
                if exp_time.tzinfo is None:
                    exp_time = exp_time.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < (exp_time - timedelta(seconds=60)):
                    return config.outlook_calendar_access_token

        # Need to refresh
        if not config.outlook_calendar_refresh_token:
            return None

        client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "")
        tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")

        if not client_id or not client_secret:
            logger.error("Microsoft OAuth credentials not configured")
            return None

        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{MS_AUTH_BASE}/{tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": config.outlook_calendar_refresh_token,
                    "grant_type": "refresh_token",
                    "scope": " ".join(TEAMS_SCOPES),
                },
            )
            resp.raise_for_status()
            tokens = resp.json()

            # Update config with new tokens
            config.outlook_calendar_access_token = tokens.get("access_token")
            if "refresh_token" in tokens:
                config.outlook_calendar_refresh_token = tokens.get("refresh_token")
            if "expires_in" in tokens:
                config.outlook_calendar_token_expires_at = datetime.now(
                    timezone.utc
                ) + timedelta(seconds=tokens.get("expires_in"))
            await db.commit()

            return tokens.get("access_token")
        except httpx.HTTPError as e:
            logger.error(f"Microsoft token refresh failed: {e}")
            return None

    def _to_utc_iso(self, dt: datetime) -> str:
        """Convert datetime to UTC ISO format."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

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
        Generate a Microsoft Teams meeting link using Graph API.
        """
        access_token = await self._get_valid_access_token(config, db)
        if not access_token:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.AUTH_EXPIRED,
                error_message="Failed to get valid Microsoft access token",
            )

        # Create online meeting
        meeting = {
            "subject": summary,
            "startDateTime": self._to_utc_iso(start_time),
            "endDateTime": self._to_utc_iso(end_time),
        }

        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{GRAPH_API_BASE}/me/onlineMeetings",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=meeting,
            )
            resp.raise_for_status()
            meeting_data = resp.json()

            join_url = meeting_data.get("joinWebUrl") or meeting_data.get("joinUrl")
            if join_url:
                return MeetingLinkResult(
                    status="READY",
                    meeting_link_url=join_url,
                    external_id=meeting_data.get("id"),
                )
            else:
                return MeetingLinkResult(
                    status="UNAVAILABLE",
                    error_code=MeetingLinkErrorCode.API_ERROR,
                    error_message="No join URL in meeting response",
                    external_id=meeting_data.get("id"),
                )

        except httpx.TimeoutException:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.TIMEOUT,
                error_message="Microsoft Graph API timeout",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return MeetingLinkResult(
                    status="UNAVAILABLE",
                    error_code=MeetingLinkErrorCode.RATE_LIMITED,
                    error_message="Microsoft API rate limit exceeded",
                )
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message=f"Microsoft API error: {e.response.status_code}",
            )
        except httpx.HTTPError as e:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message=str(e),
            )
