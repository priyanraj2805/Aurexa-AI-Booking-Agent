import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import BackgroundTasks

from app.db.database import AsyncSessionLocal
from app.crud.audit import create_audit_log
from app.schemas.audit import AuditAction, AuditLogCreate

logger = logging.getLogger(__name__)


async def log_audit_event_async(
    *,
    org_id: UUID,
    action: AuditAction,
    resource_type: str,
    resource_id: Optional[str] = None,
    actor_id: Optional[UUID] = None,
    actor_email: Optional[str] = None,
    old_values: Optional[dict[str, Any]] = None,
    new_values: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Write an audit log in an isolated DB session."""
    try:
        async with AsyncSessionLocal() as db:
            payload = AuditLogCreate(
                org_id=org_id,
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                old_values=old_values,
                new_values=new_values,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            await create_audit_log(db, payload)
    except Exception:
        logger.exception("Failed to write audit log")


def log_audit_event(
    background_tasks: BackgroundTasks,
    *,
    org_id: UUID,
    action: AuditAction,
    resource_type: str,
    resource_id: Optional[str] = None,
    actor_id: Optional[UUID] = None,
    actor_email: Optional[str] = None,
    old_values: Optional[dict[str, Any]] = None,
    new_values: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Schedule audit write as a background task."""
    background_tasks.add_task(
        log_audit_event_async,
        org_id=org_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_email=actor_email,
        old_values=old_values,
        new_values=new_values,
        ip_address=ip_address,
        user_agent=user_agent,
    )

