"""
Zoom provider implementation.

Uses Zoom API to create meetings with join URLs.
Zoom uses separate OAuth flow (not tied to calendar providers).
"""

import logging
import os
import base64
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

ZOOM_API_BASE = "https://api.zoom.us/v2"
ZOOM_AUTH_BASE = "https://zoom.us/oauth"


class ZoomProvider(MeetingProvider):
    """Zoom provider using Zoom API."""

    provider_name = "zoom"
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
        """Check if Zoom is connected."""
        return bool(config and config.zoom_connected and config.zoom_refresh_token)

    @staticmethod
    def _get_client_config(redirect_uri: Optional[str] = None) -> tuple[str, str, str]:
        client_id = os.getenv("ZOOM_CLIENT_ID", "")
        client_secret = os.getenv("ZOOM_CLIENT_SECRET", "")
        resolved_redirect_uri = redirect_uri or os.getenv(
            "ZOOM_REDIRECT_URI",
            "http://localhost:8005/api/v1/dashboard/meeting-providers/zoom/callback",
        )
        return client_id, client_secret, resolved_redirect_uri

    def get_auth_url(self, state: str = "", redirect_uri: Optional[str] = None) -> str:
        import urllib.parse

        client_id, _, redirect_uri = self._get_client_config(redirect_uri=redirect_uri)
        if not client_id:
            raise RuntimeError("ZOOM_CLIENT_ID environment variable is not set")

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
        if state:
            params["state"] = state

        return f"{ZOOM_AUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"

    async def handle_callback(
        self,
        code: str,
        config: "OrgConfig",
        db: "AsyncSession",
        redirect_uri: Optional[str] = None,
    ) -> bool:
        client_id, client_secret, redirect_uri = self._get_client_config(
            redirect_uri=redirect_uri
        )
        if not client_id:
            raise RuntimeError("ZOOM_CLIENT_ID environment variable is not set")
        if not client_secret:
            raise RuntimeError("ZOOM_CLIENT_SECRET environment variable is not set")

        client = await self.get_http_client()
        auth_string = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        try:
            resp = await client.post(
                f"{ZOOM_AUTH_BASE}/token",
                headers={
                    "Authorization": f"Basic {auth_string}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            tokens = resp.json()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in")

            if access_token:
                config.zoom_access_token = access_token
                if refresh_token:
                    config.zoom_refresh_token = refresh_token
                if expires_in:
                    config.zoom_token_expires_at = datetime.now(
                        timezone.utc
                    ) + timedelta(seconds=expires_in)
                config.zoom_connected = True
                await db.commit()
                logger.info("Zoom connected successfully")
                return True
            return False
        except httpx.HTTPError as e:
            logger.error(f"Zoom token exchange failed: {e}")
            return False

    async def _get_valid_access_token(
        self, config: "OrgConfig", db: "AsyncSession"
    ) -> Optional[str]:
        """Get valid access token, refreshing if needed."""
        # Check if current token is valid
        if config.zoom_access_token:
            if config.zoom_token_expires_at:
                exp_time = config.zoom_token_expires_at
                if exp_time.tzinfo is None:
                    exp_time = exp_time.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < (exp_time - timedelta(seconds=60)):
                    return config.zoom_access_token

        # Need to refresh
        if not config.zoom_refresh_token:
            return None

        client_id = os.getenv("ZOOM_CLIENT_ID", "")
        client_secret = os.getenv("ZOOM_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            logger.error("Zoom OAuth credentials not configured")
            return None

        client = await self.get_http_client()
        try:
            auth_string = base64.b64encode(
                f"{client_id}:{client_secret}".encode()
            ).decode()

            resp = await client.post(
                f"{ZOOM_AUTH_BASE}/token",
                headers={
                    "Authorization": f"Basic {auth_string}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": config.zoom_refresh_token,
                },
            )
            resp.raise_for_status()
            tokens = resp.json()

            # Update config with new tokens
            config.zoom_access_token = tokens.get("access_token")
            if "refresh_token" in tokens:
                config.zoom_refresh_token = tokens.get("refresh_token")
            if "expires_in" in tokens:
                config.zoom_token_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=tokens.get("expires_in")
                )
            await db.commit()

            return tokens.get("access_token")
        except httpx.HTTPError as e:
            logger.error(f"Zoom token refresh failed: {e}")
            return None

    def _to_zoom_datetime(self, dt: datetime) -> str:
        """Convert datetime to Zoom format (ISO 8601)."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _calculate_duration_minutes(self, start: datetime, end: datetime) -> int:
        """Calculate meeting duration in minutes."""
        delta = end - start
        return max(1, int(delta.total_seconds() / 60))

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
        Generate a Zoom meeting link using Zoom API.
        """
        access_token = await self._get_valid_access_token(config, db)
        if not access_token:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.AUTH_EXPIRED,
                error_message="Failed to get valid Zoom access token",
            )

        # Create meeting
        meeting = {
            "topic": summary,
            "type": 2,  # Scheduled meeting
            "start_time": self._to_zoom_datetime(start_time),
            "duration": self._calculate_duration_minutes(start_time, end_time),
            "timezone": "UTC",
            "settings": {
                "host_video": True,
                "participant_video": True,
                "join_before_host": False,
                "mute_upon_entry": True,
                "auto_recording": "none",
            },
        }

        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{ZOOM_API_BASE}/users/me/meetings",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=meeting,
            )
            resp.raise_for_status()
            meeting_data = resp.json()

            join_url = meeting_data.get("join_url")
            if join_url:
                return MeetingLinkResult(
                    status="READY",
                    meeting_link_url=join_url,
                    external_id=str(meeting_data.get("id")),
                )
            else:
                return MeetingLinkResult(
                    status="UNAVAILABLE",
                    error_code=MeetingLinkErrorCode.API_ERROR,
                    error_message="No join URL in Zoom meeting response",
                    external_id=str(meeting_data.get("id"))
                    if meeting_data.get("id")
                    else None,
                )

        except httpx.TimeoutException:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.TIMEOUT,
                error_message="Zoom API timeout",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                return MeetingLinkResult(
                    status="UNAVAILABLE",
                    error_code=MeetingLinkErrorCode.RATE_LIMITED,
                    error_message="Zoom API rate limit exceeded",
                )
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message=f"Zoom API error: {e.response.status_code}",
            )
        except httpx.HTTPError as e:
            return MeetingLinkResult(
                status="UNAVAILABLE",
                error_code=MeetingLinkErrorCode.API_ERROR,
                error_message=str(e),
            )
