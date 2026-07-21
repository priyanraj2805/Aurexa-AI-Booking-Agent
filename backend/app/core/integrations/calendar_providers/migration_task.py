"""
Calendar Provider Migration Task.

Handles migration of existing bookings when admin switches active calendar provider.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_

from app.models.booking import Booking, TimeSlot
from app.models.config import OrgConfig, CalendarProviderType
from app.core.integrations.calendar_providers.factory import get_provider
from app.core.integrations.calendar_providers.google import (
    format_google_event_description,
    format_google_event_summary,
)
from app.core.integrations.calendar_providers.outlook import (
    format_outlook_event_description,
    format_outlook_event_subject,
)

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    total_bookings: int = 0
    processed: int = 0
    successes: int = 0
    failures: int = 0
    failed_bookings: List[Tuple[str, str]] = field(default_factory=list)


async def migrate_bookings_to_new_provider(
    db: AsyncSession,
    org_config: OrgConfig,
    old_provider_type: CalendarProviderType,
    new_provider_type: CalendarProviderType,
    org_name: str = "Booking",
) -> MigrationResult:
    """
    Migrate all future bookings from old provider to new provider.

    Args:
        db: Database session
        org_config: Organization configuration
        old_provider_type: The previous active provider (source)
        new_provider_type: The new active provider (destination)
        org_name: Organization name for event formatting

    Returns:
        MigrationResult with counts and any failures
    """
    result = MigrationResult()

    if new_provider_type == CalendarProviderType.NONE:
        logger.info("New provider is NONE, skipping migration")
        return result

    new_provider = get_provider(new_provider_type.value.lower())
    if not new_provider:
        logger.error(f"Unknown new provider: {new_provider_type}")
        return result

    if not await new_provider.is_connected(org_config):
        logger.warning(
            f"New provider {new_provider_type} is not connected, skipping migration"
        )
        return result

    now = datetime.now(timezone.utc)

    booking_result = await db.execute(
        select(Booking, TimeSlot)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .where(
            and_(
                Booking.org_id == org_config.org_id,
                TimeSlot.start_time > now,
            )
        )
    )
    rows = booking_result.all()
    result.total_bookings = len(rows)

    old_provider = (
        get_provider(old_provider_type.value.lower())
        if old_provider_type != CalendarProviderType.NONE
        else None
    )

    for booking, slot in rows:
        result.processed += 1

        start_time = slot.start_time
        end_time = slot.end_time

        try:
            if old_provider and old_provider_type == CalendarProviderType.GOOGLE:
                if booking.google_event_id:
                    await old_provider.delete_event(
                        org_config, booking.google_event_id, db
                    )

            if old_provider and old_provider_type == CalendarProviderType.OUTLOOK:
                if booking.outlook_event_id:
                    await old_provider.delete_event(
                        org_config, booking.outlook_event_id, db
                    )

            event_id = await create_booking_event(
                new_provider,
                org_config,
                db,
                booking,
                start_time,
                end_time,
                new_provider_type,
                org_name,
            )

            if event_id:
                if new_provider_type == CalendarProviderType.GOOGLE:
                    booking.google_event_id = event_id
                elif new_provider_type == CalendarProviderType.OUTLOOK:
                    booking.outlook_event_id = event_id
                await db.commit()
                result.successes += 1
                logger.info(
                    f"Migrated booking {booking.id} to {new_provider_type.value}"
                )
            else:
                result.failures += 1
                result.failed_bookings.append(
                    (str(booking.id), "Failed to create event")
                )

        except Exception as e:
            logger.error(f"Failed to migrate booking {booking.id}: {e}")
            result.failures += 1
            result.failed_bookings.append((str(booking.id), str(e)))

    return result


async def create_booking_event(
    provider,
    org_config: OrgConfig,
    db: AsyncSession,
    booking: Booking,
    provider_type: CalendarProviderType,
    org_name: str,
) -> Optional[str]:
    """Create a calendar event for a booking."""

    if provider_type == CalendarProviderType.GOOGLE:
        summary = format_google_event_summary(org_name, booking.customer_name)
        description = format_google_event_description(
            booking.customer_name,
            booking.customer_email,
            booking.start_time,
            booking.end_time,
            "Migration",
            str(booking.id),
            booking.customer_phone,
            "Appointment",
            org_name,
        )
    elif provider_type == CalendarProviderType.OUTLOOK:
        summary = format_outlook_event_subject(org_name, booking.customer_name)
        description = format_outlook_event_description(
            booking.customer_name,
            booking.customer_email,
            booking.start_time,
            booking.end_time,
            "Migration",
            str(booking.id),
            booking.customer_phone,
            "Appointment",
            org_name,
        )
    else:
        return None

    return await provider.create_event(
        config=org_config,
        summary=summary,
        description=description,
        start_time=booking.start_time,
        end_time=booking.end_time,
        customer_email=booking.customer_email,
        customer_phone=booking.customer_phone,
        source_channel="Migration",
        booking_id=str(booking.id),
        db=db,
    )


async def cleanup_old_provider_events(
    db: AsyncSession,
    org_config: OrgConfig,
    old_provider_type: CalendarProviderType,
) -> int:
    """
    Delete events from the old provider for all future bookings.
    Called when disconnecting or switching away from a provider.

    Returns count of deleted events.
    """
    if old_provider_type == CalendarProviderType.NONE:
        return 0

    provider = get_provider(old_provider_type.value.lower())
    if not provider or not await provider.is_connected(org_config):
        return 0

    now = datetime.now(timezone.utc)

    booking_result = await db.execute(
        select(Booking, TimeSlot)
        .join(TimeSlot, Booking.slot_id == TimeSlot.id)
        .where(
            and_(
                Booking.org_id == org_config.org_id,
                TimeSlot.start_time > now,
            )
        )
    )
    rows = booking_result.all()

    deleted_count = 0
    for booking, slot in rows:
        try:
            event_id = None
            if old_provider_type == CalendarProviderType.GOOGLE:
                event_id = booking.google_event_id
            elif old_provider_type == CalendarProviderType.OUTLOOK:
                event_id = booking.outlook_event_id

            if event_id:
                success = await provider.delete_event(org_config, event_id, db)
                if success:
                    deleted_count += 1
                    if old_provider_type == CalendarProviderType.GOOGLE:
                        booking.google_event_id = None
                    elif old_provider_type == CalendarProviderType.OUTLOOK:
                        booking.outlook_event_id = None
        except Exception as e:
            logger.warning(f"Failed to delete event for booking {booking.id}: {e}")

    await db.commit()
    return deleted_count
