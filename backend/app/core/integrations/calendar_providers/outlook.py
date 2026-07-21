"""
Microsoft Outlook Calendar provider implementation using Microsoft Graph API.

Inherits from CalendarProvider base class.
Supports OAuth 2.0 authorization code flow for web applications.
Scopes: Calendars.ReadWrite, User.Read
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

import httpx

from app.core.integrations.calendar_providers import CalendarProvider

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
MS_AUTH_BASE = "https://login.microsoftonline.com"

SCOPES = ["Calendars.ReadWrite", "User.Read"]


class OutlookCalendarProvider(CalendarProvider):
    provider_name = "outlook"
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

    @staticmethod
    def _get_client_config() -> tuple[str, str, str]:
        client_id = os.getenv("MICROSOFT_CLIENT_ID", "")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "")
        tenant_id = os.getenv("MICROSOFT_TENANT_ID", "common")
        return client_id, client_secret, tenant_id

    async def is_enabled(self, config) -> bool:
        return bool(config and config.outlook_calendar_enabled)

    async def is_connected(self, config) -> bool:
        return bool(
            config
            and config.outlook_calendar_connected
            and config.outlook_calendar_refresh_token
        )

    def get_auth_url(self, state: str = "", redirect_uri: Optional[str] = None) -> str:
        import urllib.parse

        client_id, _, tenant_id = OutlookCalendarProvider._get_client_config()
        if not client_id:
            raise RuntimeError("MICROSOFT_CLIENT_ID environment variable is not set")
        redirect_uri = redirect_uri or os.getenv(
            "MICROSOFT_REDIRECT_URI",
            "http://localhost:8005/api/v1/dashboard/outlook-calendar/callback",
        )
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "response_mode": "query",
            "state": state,
        }
        return f"{MS_AUTH_BASE}/{tenant_id}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    async def handle_callback(
        self, code: str, config, db, redirect_uri: Optional[str] = None
    ) -> bool:
        client_id, client_secret, tenant_id = (
            OutlookCalendarProvider._get_client_config()
        )
        if not client_id:
            raise RuntimeError("MICROSOFT_CLIENT_ID environment variable is not set")
        if not client_secret:
            raise RuntimeError("MICROSOFT_CLIENT_SECRET environment variable is not set")
        redirect_uri = redirect_uri or os.getenv(
            "MICROSOFT_REDIRECT_URI",
            "http://localhost:8005/api/v1/dashboard/outlook-calendar/callback",
        )
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{MS_AUTH_BASE}/{tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                    "scope": " ".join(SCOPES),
                },
            )
            resp.raise_for_status()
            tokens = resp.json()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in")
            if access_token:
                config.outlook_calendar_access_token = access_token
                if refresh_token:
                    config.outlook_calendar_refresh_token = refresh_token
                if expires_in:
                    config.outlook_calendar_token_expires_at = datetime.now(
                        timezone.utc
                    ) + timedelta(seconds=expires_in)
                config.outlook_calendar_connected = True
                config.outlook_calendar_enabled = True
                await db.commit()
                logger.info("Outlook Calendar connected successfully")
                return True
        except httpx.HTTPStatusError as e:
            error_code = ""
            try:
                error_code = e.response.json().get("error", "")
            except ValueError:
                error_code = ""
            logger.error(
                f"Outlook Calendar token exchange failed: {e}; error_code={error_code}"
            )
            if error_code == "unauthorized_client":
                raise RuntimeError("outlook_unauthorized_client")
        except httpx.HTTPError as e:
            logger.error(f"Outlook Calendar token exchange failed: {e}")
            return False

    async def disconnect(self, config, db) -> None:
        config.outlook_calendar_connected = False
        config.outlook_calendar_refresh_token = None
        config.outlook_calendar_access_token = None
        config.outlook_calendar_token_expires_at = None
        await db.commit()

    async def _refresh_access_token(
        self, config, db, refresh_token: str
    ) -> Optional[str]:
        client_id, client_secret, tenant_id = (
            OutlookCalendarProvider._get_client_config()
        )
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{MS_AUTH_BASE}/{tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": " ".join(SCOPES),
                },
            )
            resp.raise_for_status()
            tokens = resp.json()

            if config and db:
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
            logger.error(f"Outlook Calendar token refresh failed: {e}")
            return None

    def _is_token_expired(self, config) -> bool:
        if not config.outlook_calendar_token_expires_at:
            return True
        try:
            exp_time = config.outlook_calendar_token_expires_at
            if exp_time.tzinfo is None:
                exp_time = exp_time.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= (exp_time - timedelta(seconds=60))
        except (ValueError, TypeError, AttributeError):
            return True

    async def _get_valid_access_token(self, config, db) -> Optional[str]:
        if config.outlook_calendar_access_token and not self._is_token_expired(config):
            return config.outlook_calendar_access_token
        if config.outlook_calendar_refresh_token:
            new_token = await self._refresh_access_token(
                config, db, config.outlook_calendar_refresh_token
            )
            return new_token
        return None

    def _to_utc_iso(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    async def get_busy_periods(
        self,
        config,
        start_time: datetime,
        end_time: datetime,
        db=None,
    ) -> List[Tuple[datetime, datetime]]:
        if not await self.is_connected(config):
            return []

        access_token = await self._get_valid_access_token(config, db)
        if not access_token:
            return []

        events = await self._fetch_calendar_view_http(
            access_token, start_time, end_time
        )

        busy_periods = []
        for event in events:
            start_dt = self._parse_outlook_datetime(event.get("start"))
            end_dt = self._parse_outlook_datetime(event.get("end"))

            if start_dt and end_dt:
                busy_periods.append((start_dt, end_dt))

        return busy_periods

    async def _fetch_calendar_view_http(
        self,
        access_token: str,
        time_min: datetime,
        time_max: datetime,
    ) -> List[Dict[str, Any]]:
        client = await self.get_http_client()
        params = {
            "$orderby": "start/dateTime asc",
            "$top": "50",
            "startDateTime": self._to_utc_iso(time_min),
            "endDateTime": self._to_utc_iso(time_max),
        }
        try:
            resp = await client.get(
                f"{GRAPH_API_BASE}/me/calendar/calendarView",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json().get("value", [])
        except httpx.HTTPError as e:
            logger.error(f"Outlook Calendar calendarView failed: {e}")
            return []

    def _parse_outlook_datetime(
        self, dt_dict: Optional[Dict[str, str]]
    ) -> Optional[datetime]:
        if not dt_dict:
            return None

        date_time_str = dt_dict.get("dateTime")
        time_zone = dt_dict.get("timeZone", "UTC")

        if date_time_str:
            try:
                if time_zone:
                    dt = datetime.fromisoformat(date_time_str.replace("Z", "+00:00"))
                    if "+" in time_zone or time_zone.startswith("UTC"):
                        return dt.astimezone(timezone.utc)
                    return dt.replace(tzinfo=timezone.utc)
                return datetime.fromisoformat(date_time_str).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return None

        return None

    async def _create_event_http(
        self,
        access_token: str,
        subject: str,
        body_content: str,
        start_iso: str,
        end_iso: str,
        attendees_email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        event: Dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": body_content,
            },
            "start": {"dateTime": start_iso, "timeZone": "UTC"},
            "end": {"dateTime": end_iso, "timeZone": "UTC"},
            "isAllDay": False,
            "showAs": "busy",
        }
        if attendees_email:
            event["attendees"] = [
                {"emailAddress": {"address": attendees_email}, "type": "required"}
            ]
        client = await self.get_http_client()
        try:
            resp = await client.post(
                f"{GRAPH_API_BASE}/me/events",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Outlook Calendar create event failed: {e}")
            return None

    async def _update_event_http(
        self,
        access_token: str,
        event_id: str,
        subject: str,
        body_content: str,
        start_iso: str,
        end_iso: str,
    ) -> bool:
        event: Dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "text",
                "content": body_content,
            },
            "start": {"dateTime": start_iso, "timeZone": "UTC"},
            "end": {"dateTime": end_iso, "timeZone": "UTC"},
        }
        client = await self.get_http_client()
        try:
            resp = await client.patch(
                f"{GRAPH_API_BASE}/me/events/{event_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Outlook Calendar update event failed: {e}")
            return False

    async def _delete_event_http(self, access_token: str, event_id: str) -> bool:
        client = await self.get_http_client()
        try:
            resp = await client.delete(
                f"{GRAPH_API_BASE}/me/events/{event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Outlook Calendar delete event failed: {e}")
            return False

    async def _fetch_events_http(
        self,
        access_token: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        params = {"$orderby": "start/dateTime asc", "$top": "50"}

        if time_min:
            params["$filter"] = f"start/dateTime ge '{self._to_utc_iso(time_min)}'"
        if time_max:
            prefix = (
                f"start/dateTime ge '{self._to_utc_iso(time_min)}' and "
                if time_min
                else ""
            )
            params["$filter"] = (
                f"{prefix}end/dateTime le '{self._to_utc_iso(time_max)}'"
            )

        client = await self.get_http_client()
        try:
            resp = await client.get(
                f"{GRAPH_API_BASE}/me/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json().get("value", [])
        except httpx.HTTPError as e:
            logger.error(f"Outlook Calendar fetch events failed: {e}")
            return []

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
        access_token = await self._get_valid_access_token(config, db)
        if not access_token:
            return None
        start_iso = self._to_utc_iso(start_time)
        end_iso = self._to_utc_iso(end_time)
        event = await self._create_event_http(
            access_token,
            summary,
            description,
            start_iso,
            end_iso,
            customer_email,
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
        access_token = await self._get_valid_access_token(config, db)
        if not access_token:
            return False
        start_iso = self._to_utc_iso(start_time)
        end_iso = self._to_utc_iso(end_time)
        return await self._update_event_http(
            access_token, event_id, summary, description, start_iso, end_iso
        )

    async def delete_event(self, config, event_id: str, db) -> bool:
        access_token = await self._get_valid_access_token(config, db)
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
        access_token = await self._get_valid_access_token(config, db)
        if not access_token:
            return []
        return await self._fetch_events_http(access_token, time_min, time_max)


def format_outlook_event_description(
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
    lines = [
        "BOOKING CONFIRMATION",
        "=" * 30,
        "",
        "CUSTOMER DETAILS",
        "-" * 30,
        f"  Name:  {customer_name}",
        f"  Email: {customer_email}",
    ]
    if customer_phone:
        lines.append(f"  Phone: {customer_phone}")
    lines.extend(
        [
            "",
            "APPOINTMENT DETAILS",
            "-" * 30,
            f"  Date:  {start_time.strftime('%A, %B %d, %Y')}",
            f"  Time:  {start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}",
            f"  Type:  {service_type}",
            f"  Source: {source_channel}",
            "",
            "REFERENCE",
            "-" * 30,
            f"  Booking ID: {booking_id}",
            "",
            "Booked via DotStark AI Booking Agent",
        ]
    )
    return "\n".join(lines)


def format_outlook_event_subject(
    org_name: str, customer_name: str, service_type: str = "Appointment"
) -> str:
    return f"{org_name}: {customer_name} - {service_type}"
