"""
Google Calendar integration service.

Provides functionality to:
- Authenticate with Google Calendar API using OAuth 2.0
- Fetch events from Google Calendar
- Sync bookings to Google Calendar (unidirectional: our bookings → Google)

Note: For production, you need to:
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable Google Calendar API
3. Create OAuth 2.0 credentials
4. Add your domain to authorized origins
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Google Calendar API settings from environment variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8005/api/v1/dashboard/google-calendar/callback",
)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


def get_google_auth_url(state: str = "", redirect_uri: Optional[str] = None) -> str:
    """
    Generate Google OAuth 2.0 authorization URL.
    User visits this URL to grant access to their Google Calendar.
    """
    import urllib.parse

    redirect_uri = redirect_uri or os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8005/api/v1/dashboard/google-calendar/callback",
    )
    if not GOOGLE_CLIENT_ID:
        raise RuntimeError("GOOGLE_CLIENT_ID environment variable is not set")

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }

    if state:
        params["state"] = state

    return (
        f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    )


async def exchange_code_for_tokens(
    code: str, redirect_uri: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Exchange authorization code for access and refresh tokens.
    """
    redirect_uri = redirect_uri or os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8005/api/v1/dashboard/google-calendar/callback",
    )
    if not GOOGLE_CLIENT_ID:
        raise RuntimeError("GOOGLE_CLIENT_ID environment variable is not set")
    if not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("GOOGLE_CLIENT_SECRET environment variable is not set")

    HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to exchange code for tokens: {e}")
            return None


async def refresh_access_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    Refresh access token using refresh token.
    """
    HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to refresh access token: {e}")
            return None


async def get_calendar_events(
    access_token: str,
    calendar_id: str = "primary",
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetch events from Google Calendar.

    Args:
        access_token: Google OAuth access token
        calendar_id: Calendar ID (default: 'primary')
        time_min: Start of time range (default: now)
        time_max: End of time range
        max_results: Maximum number of events to fetch

    Returns:
        List of calendar events
    """
    import urllib.parse

    if time_min is None:
        time_min = datetime.now(timezone.utc)

    params = {
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
        "timeMin": time_min.isoformat(),
    }

    if time_max:
        params["timeMax"] = time_max.isoformat()

    encoded_calendar_id = urllib.parse.quote(calendar_id, safe="")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{CALENDAR_API_BASE}/calendars/{encoded_calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("items", [])
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch calendar events: {e}")
            return []


async def create_calendar_event(
    access_token: str,
    summary: str,
    description: str,
    start_time: datetime,
    end_time: datetime,
    calendar_id: str = "primary",
) -> Optional[Dict[str, Any]]:
    """
    Create an event in Google Calendar.

    Args:
        access_token: Google OAuth access token
        summary: Event title
        description: Event description
        start_time: Event start time
        end_time: Event end time
        calendar_id: Calendar ID (default: 'primary')

    Returns:
        Created event data or None on failure
    """
    import urllib.parse

    # Normalize to UTC
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    else:
        start_time = start_time.astimezone(timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    else:
        end_time = end_time.astimezone(timezone.utc)

    event = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "UTC",
        },
    }

    encoded_calendar_id = urllib.parse.quote(calendar_id, safe="")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{CALENDAR_API_BASE}/calendars/{encoded_calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                json=event,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to create calendar event: {e}")
            return None


async def delete_calendar_event(
    access_token: str,
    event_id: str,
    calendar_id: str = "primary",
) -> bool:
    """
    Delete an event from Google Calendar.

    Returns:
        True on success, False on failure
    """
    import urllib.parse

    encoded_calendar_id = urllib.parse.quote(calendar_id, safe="")
    encoded_event_id = urllib.parse.quote(event_id, safe="")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.delete(
                f"{CALENDAR_API_BASE}/calendars/{encoded_calendar_id}/events/{encoded_event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to delete calendar event: {e}")
            return False


async def create_booking_calendar_event(
    org_id: str,
    refresh_token: str,
    customer_name: str,
    customer_email: str,
    start_time: datetime,
    end_time: datetime,
    booking_id: str,
    customer_phone: Optional[str] = None,
    source_channel: str = "Web Widget",
    service_type: str = "Consultation",
    org_name: str = "DotStark",
    display_timezone: str = "UTC",
) -> Optional[str]:
    """
    Create a Google Calendar event for a booking.

    This function handles:
    1. Refreshing the access token using the stored refresh token
    2. Creating the calendar event
    3. Returning the event ID to store in the booking record

    Args:
        org_id: Organization ID (for logging)
        refresh_token: OAuth refresh token from org config
        customer_name: Name of the customer
        customer_email: Email of the customer
        start_time: Booking start time
        end_time: Booking end time
        booking_id: Internal booking ID (for the event description)
        customer_phone: Phone number of the customer (optional)
        source_channel: Where the booking was made (Web Widget, WhatsApp, etc.)
        service_type: Type of service/appointment
        org_name: Organization name for the event title

    Returns:
        Google Calendar event ID if successful, None on failure
    """
    try:
        # Normalize event display formatting to business timezone for description text.
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        try:
            display_zone = ZoneInfo(display_timezone)
            normalized_display_tz = display_zone.key
        except Exception:
            display_zone = ZoneInfo("UTC")
            normalized_display_tz = "UTC"

        display_start = start_time.astimezone(display_zone)
        display_end = end_time.astimezone(display_zone)

        tokens = await refresh_access_token(refresh_token)
        if not tokens or "access_token" not in tokens:
            logger.error(f"Failed to refresh token for org {org_id}")
            return None

        access_token = tokens["access_token"]

        # Map source to emoji
        source_emoji = "💻" if source_channel.lower() == "web widget" else "💬"

        # Format the event title with org name and service
        summary = f"📅 {org_name}: {customer_name} - {service_type}"

        # Build detailed description with separators
        separator = "─" * 25
        description_parts = [
            f"╔{'═' * 33}╗",
            f"║     BOOKING CONFIRMATION        ║",
            f"╚{'═' * 33}╝",
            f"",
            f"👤 CUSTOMER DETAILS",
            f"{separator}",
            f"  Name:    {customer_name}",
            f"  Email:   {customer_email}",
        ]

        if customer_phone:
            description_parts.append(f"  Phone:   {customer_phone}")

        description_parts.extend(
            [
                f"",
                f"📅 APPOINTMENT DETAILS",
                f"{separator}",
                f"  Date:    {display_start.strftime('%A, %B %d, %Y')}",
                f"  Time:    {display_start.strftime('%I:%M %p')} - {display_end.strftime('%I:%M %p')} ({normalized_display_tz})",
                f"  Type:    {service_type}",
                f"  Source:  {source_channel} {source_emoji}",
                f"",
                f"🔒 REFERENCE",
                f"{separator}",
                f"  Booking ID: {booking_id}",
                f"",
                f"  Booked via DotStark AI Booking Agent",
            ]
        )

        description = "\n".join(description_parts)

        event = await create_calendar_event(
            access_token=access_token,
            summary=summary,
            description=description,
            start_time=start_time,
            end_time=end_time,
        )

        if event and "id" in event:
            logger.info(
                f"Created Google Calendar event {event['id']} for booking {booking_id}"
            )
            return event["id"]
        else:
            logger.error(f"Failed to create calendar event for booking {booking_id}")
            return None

    except Exception as e:
        logger.error(f"Error creating calendar event for org {org_id}: {e}")
        return None
