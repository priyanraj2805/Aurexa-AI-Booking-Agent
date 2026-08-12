"""In-app notification helper (Phase 16).

Writes a per-admin notification row surfaced by the dashboard bell. Callers
pass the recipient admin_id and a kind used by the UI for routing (e.g.
'impersonation.approved'). Fail-closed on commit: the caller owns the
transaction.
"""
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

NOTIFICATION_FIELDS = (
    "id, kind, title, body, resource_type, resource_id, is_read, created_at, read_at"
)


async def create_inapp_notification(
    db: AsyncSession,
    admin_id: UUID,
    kind: str,
    title: str,
    body: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> UUID:
    """Insert one notification for a super admin (unread). Does not commit —
    the caller commits with the rest of its transaction."""
    row = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.inapp_notifications
                    (admin_id, kind, title, body, resource_type, resource_id)
                VALUES (:admin_id, :kind, :title, :body, :resource_type, :resource_id)
                RETURNING id
                """
            ),
            {
                "admin_id": admin_id,
                "kind": kind[:40],
                "title": title[:255],
                "body": (body or "")[:4000] or None,
                "resource_type": resource_type[:60] if resource_type else None,
                "resource_id": resource_id[:64] if resource_id else None,
            },
        )
    ).scalar()
    return row
