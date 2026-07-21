"""
Google Calendar provider implementation.

Inherits from CalendarProvider base class.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import httpx

from app.core.integrations.calendar_providers import CalendarProvider

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarProvider(CalendarProvider):
    provider_name = "google"
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

    async def is_enabled(self, config) -> bool:
        return bool(config and config.google_calendar_enabled)

    async def is_connected(self, config) -> bool:
        return bool(
            config
            and config.google_calendar_connected
            and config.google_calendar_refresh_token
        )

    def get_auth_url(self, state: str = "") -> str:
        import urllib.parse

        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        if not redirect_uri:
            raise RuntimeError("GOOGLE_REDIRECT_URI environment variable is not set")
        if not client_id:
            raise RuntimeError("GOOGLE_CLIENT_ID environment variable is not set")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

    def get_frontend_callback_url(self, base_url: str = "http://localhost:3002") -> str:
        return f"{base_url.rstrip('/')}/dashboard/calendar"

    async def handle_callback(self, code: str, config, db) -> bool:
        import urllib.parse

        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        if not redirect_uri:
            raise RuntimeError("GOOGLE_REDIRECT_URI environment variable is not set")
        client = await self.get_http_client()
        try:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            tokens = resp.json()
            if "refresh_token" in tokens:
                config.google_calendar_refresh_token = tokens["refresh_token"]
                config.google_calendar_connected = True
                config.google_calendar_enabled = True
                await db.commit()
                return True
        except httpx.HTTPError as e:
            logger.error(f"Google Calendar token exchange failed: {e}")
        return False

    async def disconnect(self, config, db) -> None:
        config.google_calendar_connected = False
        config.google_calendar_enabled = False
        config.google_calendar_refresh_token = None
        await db.commit()

    async def _refresh_token(self, refresh_token: str) -> Optional[str]:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
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
            logger.error(f"Google Calendar token refresh failed: {e}")
            return None

    async def get_busy_periods(
        self,
        config,
        start_time: datetime,
        end_time: datetime,
        db=None,
    ) -> List[Tuple[datetime, datetime]]:
        if not await self.is_connected(config):
            return []

        access_token = await self._refresh_token(config.google_calendar_refresh_token)
        if not access_token:
            return []

        events = await self._fetch_events_http(access_token, start_time, end_time)

        busy_periods = []
        for event in events:
            if event.get("status") == "confirmed":
                start = event.get("start", {})
                end = event.get("end", {})

                start_dt = self._parse_google_datetime(start)
                end_dt = self._parse_google_datetime(end)

                if start_dt and end_dt:
                    busy_periods.append((start_dt, end_dt))

        return busy_periods

    def _parse_google_datetime(self, dt_dict: Dict[str, str]) -> Optional[datetime]:
        if not dt_dict:
            return None

        date_time_str = dt_dict.get("dateTime")
        if date_time_str:
            try:
                dt = datetime.fromisoformat(date_time_str.replace("Z", "+00:00"))
                return dt.astimezone(timezone.utc)
            except ValueError:
                return None

        date_str = dt_dict.get("date")
        if date_str:
            try:
                return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            except ValueError:
                return None

        return None

    async def _fetch_events_http(
        self,
        access_token: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        if time_min is None:
            time_min = datetime.now(timezone.utc)
        params = {
            "maxResults": 50,
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min.isoformat(),
        }
        if time_max:
            params["timeMax"] = time_max.isoformat()
        client = await self.get_http_client()
        try:
            resp = await client.get(
                f"{CALENDAR_API_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except httpx.HTTPError as e:
            logger.error(f"Google Calendar fetch events failed: {e}")
            return []

    async def _create_event_http(
        self,
        access_token: str,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[Dict[str, Any]]:
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "UTC"},
        }
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{CALENDAR_API_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {access_token}"},
                json=event,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Google Calendar create event failed: {e}")
            return None

    async def _update_event_http(
        self,
        access_token: str,
        event_id: str,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "UTC"},
        }
        client = await self.get_http_client()
        try:
            resp = await client.patch(
                f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json=event,
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Google Calendar update event failed: {e}")
            return False

    async def _delete_event_http(self, access_token: str, event_id: str) -> bool:
        client = await self.get_http_client()
        try:
            resp = await client.delete(
                f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Google Calendar delete event failed: {e}")
            return False

    async def create_event(
        self,
        config,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
        customer_email: str,
        customer_phone: Optional[str],
        source_channel: str,
        booking_id: str,
        db,
    ) -> Optional[str]:
        access_token = await self._refresh_token(config.google_calendar_refresh_token)
        if not access_token:
            return None
        event = await self._create_event_http(
            access_token, summary, description, start_time, end_time
        )
        if event and "id" in event:
            return event["id"]
        return None

    async def update_event(
        self,
        config,
        event_id: str,
        summary: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
        db,
    ) -> bool:
        if not await self.is_connected(config):
            return False
        access_token = await self._refresh_token(config.google_calendar_refresh_token)
        if not access_token:
            return False
        return await self._update_event_http(
            access_token, event_id, summary, description, start_time, end_time
        )

    async def delete_event(self, config, event_id: str, db) -> bool:
        if not await self.is_connected(config):
            return False
        access_token = await self._refresh_token(config.google_calendar_refresh_token)
        if not access_token:
            return False
        return await self._delete_event_http(access_token, event_id)

    async def get_events(
        self,
        config,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        db=None,
    ) -> List[Dict[str, Any]]:
        if not await self.is_connected(config):
            return []
        access_token = await self._refresh_token(config.google_calendar_refresh_token)
        if not access_token:
            return []
        return await self._fetch_events_http(access_token, time_min, time_max)


def format_google_event_description(
    customer_name: str,
    customer_email: str,
    start_time: datetime,
    end_time: datetime,
    source_channel: str,
    booking_id: str,
    customer_phone: Optional[str] = None,
    service_type: str = "Appointment",
    org_name: str = "Booking",
) -> str:
    separator = "-" * 25
    lines = [
        "BOOKING CONFIRMATION",
        separator,
        f"Customer: {customer_name}",
        f"Email:    {customer_email}",
    ]
    if customer_phone:
        lines.append(f"Phone:    {customer_phone}")
    lines.extend(
        [
            "",
            "APPOINTMENT DETAILS",
            separator,
            f"Date:     {start_time.strftime('%A, %B %d, %Y')}",
            f"Time:     {start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}",
            f"Type:     {service_type}",
            f"Source:   {source_channel}",
            "",
            "REFERENCE",
            separator,
            f"Booking ID: {booking_id}",
            "",
            "Booked via DotStark AI Booking Agent",
        ]
    )
    return "\n".join(lines)


def format_google_event_summary(
    org_name: str, customer_name: str, service_type: str = "Appointment"
) -> str:
    return f"{org_name}: {customer_name} - {service_type}"
