"""
Calendar provider factory and sync utilities.

Provides a unified interface for syncing bookings to connected calendar providers.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from app.core.integrations.calendar_providers import CalendarProvider
from app.core.integrations.calendar_providers.google import (
    GoogleCalendarProvider,
    format_google_event_description,
    format_google_event_summary,
)
from app.core.integrations.calendar_providers.outlook import (
    OutlookCalendarProvider,
    format_outlook_event_description,
    format_outlook_event_subject,
)

if TYPE_CHECKING:
    from app.models.config import OrgConfig, CalendarProviderType


google_provider = GoogleCalendarProvider()
outlook_provider = OutlookCalendarProvider()


def get_provider(name: str) -> Optional[CalendarProvider]:
    """Get a calendar provider by name."""
    if name == "google":
        return google_provider
    if name == "outlook":
        return outlook_provider
    return None


def get_active_provider(config: "OrgConfig") -> Optional[CalendarProvider]:
    """Get the active calendar provider based on config.active_calendar_provider."""
    if not config or not config.active_calendar_provider:
        return None

    provider_name = (
        config.active_calendar_provider.value.lower()
        if hasattr(config.active_calendar_provider, "value")
        else str(config.active_calendar_provider).lower()
    )

    if provider_name in ("none", "NONE"):
        return None

    return get_provider(provider_name)


async def get_all_connected_providers(config: "OrgConfig") -> List[CalendarProvider]:
    """Return all providers that are currently connected for the given config."""
    providers: List[CalendarProvider] = []
    if await google_provider.is_connected(config):
        providers.append(google_provider)
    if await outlook_provider.is_connected(config):
        providers.append(outlook_provider)
    return providers


async def sync_booking_to_calendars(
    config,
    db,
    customer_name: str,
    customer_email: str,
    start_time: datetime,
    end_time: datetime,
    booking_id: str,
    customer_phone: Optional[str] = None,
    source_channel: str = "Web Widget",
    service_type: str = "Appointment",
    org_name: str = "Booking",
) -> Dict[str, Optional[str]]:
    """
    Sync a booking confirmation to the active calendar provider only.

    Returns a dict mapping provider name to the created event ID (or None on failure).
    """
    import logging

    logger = logging.getLogger(__name__)
    results: Dict[str, Optional[str]] = {}

    active_provider = get_active_provider(config)
    if not active_provider:
        logger.debug("No active calendar provider, skipping sync")
        return results

    name = active_provider.provider_name
    try:
        if name == "google":
            summary = format_google_event_summary(org_name, customer_name, service_type)
            description = format_google_event_description(
                customer_name,
                customer_email,
                start_time,
                end_time,
                source_channel,
                booking_id,
                customer_phone,
                service_type,
                org_name,
            )
        elif name == "outlook":
            summary = format_outlook_event_subject(
                org_name, customer_name, service_type
            )
            description = format_outlook_event_description(
                customer_name,
                customer_email,
                start_time,
                end_time,
                source_channel,
                booking_id,
                customer_phone,
                service_type,
                org_name,
            )
        else:
            return results

        if not await active_provider.is_connected(config):
            logger.warning(f"Active provider {name} is not connected, skipping sync")
            return results

        event_id = await active_provider.create_event(
            config=config,
            summary=summary,
            description=description,
            start_time=start_time,
            end_time=end_time,
            customer_email=customer_email,
            customer_phone=customer_phone,
            source_channel=source_channel,
            booking_id=booking_id,
            db=db,
        )
        results[name] = event_id
        if event_id:
            logger.info(
                f"Created {name} Calendar event {event_id} for booking {booking_id}"
            )
        else:
            logger.error(
                f"Failed to create {name} Calendar event for booking {booking_id}"
            )
    except Exception as e:
        logger.error(f"Error syncing to {name} Calendar: {e}")
        results[name] = None

    return results
