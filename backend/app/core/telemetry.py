"""Telemetry counters for the Super Admin control plane (Phase 2).

Writes monthly-partitioned usage counters into super_admin.usage_events
(bookings, AI tokens, API calls, lifecycle actions, ...). Fully gated by
TELEMETRY_ENABLED — every call is a no-op while the gate is false, so the
main backend stays 100% behavior-identical until the GA cutover.

Partitions are auto-created by the DB trigger (super_admin.ensure_usage_partition).
"""
import logging
from uuid import UUID

from sqlalchemy import text

from app.core.config import settings
from app.db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def increment_usage(
    org_id: UUID, metric: str, delta: int = 1, *, commit: bool = True
) -> None:
    """Increment a monthly usage counter for an organization."""
    if not settings.TELEMETRY_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO super_admin.usage_events
                        (event_month, org_id, metric, count, updated_at)
                    VALUES (date_trunc('month', now())::date, :org_id, :metric, :delta, now())
                    ON CONFLICT (event_month, org_id, metric)
                    DO UPDATE SET
                        count = super_admin.usage_events.count + :delta,
                        updated_at = now()
                    """
                ),
                {"org_id": org_id, "metric": metric, "delta": delta},
            )
            if commit:
                await db.commit()
    except Exception:
        logger.exception("Failed to write usage event metric=%s org=%s", metric, org_id)
