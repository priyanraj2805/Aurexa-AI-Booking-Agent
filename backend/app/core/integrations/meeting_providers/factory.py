"""
Meeting provider factory for dispatching to provider implementations.

Resolves provider implementation from active_meeting_provider enum value.
Decoupled from calendar_providers (D-04).
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.models.config import ActiveMeetingProvider

from app.core.integrations.meeting_providers import (
    MeetingProvider,
    MeetingLinkResult,
    MeetingLinkErrorCode,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.config import OrgConfig

logger = logging.getLogger(__name__)

# Lazy-load provider instances
_provider_instances: dict[ActiveMeetingProvider, MeetingProvider] = {}


def _get_provider_instance(
    provider_type: ActiveMeetingProvider,
) -> Optional[MeetingProvider]:
    """Get or create provider instance for the given type."""
    if provider_type == ActiveMeetingProvider.NONE:
        return None

    if provider_type not in _provider_instances:
        if provider_type == ActiveMeetingProvider.GOOGLE_MEET:
            from app.core.integrations.meeting_providers.google_meet import (
                GoogleMeetProvider,
            )

            _provider_instances[provider_type] = GoogleMeetProvider()
        elif provider_type == ActiveMeetingProvider.MICROSOFT_TEAMS:
            from app.core.integrations.meeting_providers.microsoft_teams import (
                MicrosoftTeamsProvider,
            )

            _provider_instances[provider_type] = MicrosoftTeamsProvider()
        elif provider_type == ActiveMeetingProvider.ZOOM:
            from app.core.integrations.meeting_providers.zoom import ZoomProvider

            _provider_instances[provider_type] = ZoomProvider()

    return _provider_instances.get(provider_type)


def get_active_meeting_provider(config: "OrgConfig") -> Optional[MeetingProvider]:
    """Get the active meeting provider based on config.active_meeting_provider."""
    if not config or not config.active_meeting_provider:
        return None

    provider_type = config.active_meeting_provider
    if provider_type == ActiveMeetingProvider.NONE:
        return None

    return _get_provider_instance(provider_type)


async def generate_meeting_link(
    config: "OrgConfig",
    db: "AsyncSession",
    booking_id: str,
    start_time: datetime,
    end_time: datetime,
    summary: str,
    attendee_email: Optional[str] = None,
) -> MeetingLinkResult:
    """
    Generate a meeting link using the active meeting provider.

    Returns structured MeetingLinkResult — never raises exceptions for
    expected failures. This enables deterministic degraded handling.
    """
    if not config:
        return MeetingLinkResult(
            status="UNAVAILABLE",
            error_code=MeetingLinkErrorCode.NOT_CONNECTED,
            error_message="Organization config not found",
        )

    provider = get_active_meeting_provider(config)
    if not provider:
        return MeetingLinkResult(
            status="UNAVAILABLE",
            error_code=MeetingLinkErrorCode.NOT_CONNECTED,
            error_message="No active meeting provider configured",
        )

    # Check if provider is connected
    is_connected = await provider.is_connected(config)
    if not is_connected:
        return MeetingLinkResult(
            status="UNAVAILABLE",
            error_code=MeetingLinkErrorCode.NOT_CONNECTED,
            error_message=f"{provider.provider_name} is not connected",
        )

    try:
        result = await provider.generate_meeting_link(
            config=config,
            db=db,
            booking_id=booking_id,
            start_time=start_time,
            end_time=end_time,
            summary=summary,
            attendee_email=attendee_email,
        )
        return result
    except Exception as e:
        logger.error(f"Meeting link generation failed: {e}", exc_info=True)
        return MeetingLinkResult(
            status="UNAVAILABLE",
            error_code=MeetingLinkErrorCode.UNKNOWN,
            error_message=str(e),
        )
