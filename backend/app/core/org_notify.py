"""Org-scoped in-app notification helper.

Writes rows to org_notifications using a SEPARATE short-lived session so a
notification failure can never break the surrounding booking/cancellation
transaction. All failures are logged and swallowed.
"""
import logging

from uuid import UUID

from app.db.database import AsyncSessionLocal
from app.models.org_notification import OrgNotification

logger = logging.getLogger(__name__)


async def notify_org(
    org_id: UUID | str,
    kind: str,
    title: str,
    body: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    """Create an in-app notification for the org. Best-effort, never raises."""
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                OrgNotification(
                    org_id=org_id if isinstance(org_id, UUID) else UUID(str(org_id)),
                    kind=kind,
                    title=title,
                    body=body,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else None,
                )
            )
            await session.commit()
    except Exception:
        logger.warning(
            "Failed to create org notification (kind=%s org=%s)",
            kind,
            org_id,
            exc_info=True,
        )
