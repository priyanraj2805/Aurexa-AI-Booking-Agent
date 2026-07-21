from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, desc, func
from typing import List
from datetime import datetime
from uuid import UUID

from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogCreate, AuditLogFilter


async def create_audit_log(db: AsyncSession, audit_data: AuditLogCreate) -> AuditLog:
    """
    Create an audit log entry.
    
    NOTE: This should ALWAYS be called from a background task,
    never in the same transaction as the audited operation.
    """
    audit_entry = AuditLog(
        org_id=audit_data.org_id,
        actor_id=audit_data.actor_id,
        actor_email=audit_data.actor_email,
        action=audit_data.action.value if hasattr(audit_data.action, 'value') else audit_data.action,
        resource_type=audit_data.resource_type,
        resource_id=audit_data.resource_id,
        old_values=audit_data.old_values,
        new_values=audit_data.new_values,
        ip_address=audit_data.ip_address,
        user_agent=audit_data.user_agent,
    )
    db.add(audit_entry)
    await db.commit()
    await db.refresh(audit_entry)
    return audit_entry


async def get_audit_logs(
    db: AsyncSession,
    org_id: UUID,
    filters: AuditLogFilter
) -> tuple[List[AuditLog], int]:
    """
    Query audit logs with filtering and pagination.
    Returns (logs, total_count).
    """
    # Base query with org_id isolation
    conditions = [AuditLog.org_id == org_id]
    
    if filters.start_date:
        conditions.append(AuditLog.created_at >= filters.start_date)
    if filters.end_date:
        conditions.append(AuditLog.created_at <= filters.end_date)
    if filters.action:
        conditions.append(AuditLog.action == filters.action)
    if filters.actor_email:
        conditions.append(AuditLog.actor_email.ilike(f"%{filters.actor_email}%"))
    if filters.resource_type:
        conditions.append(AuditLog.resource_type == filters.resource_type)
    
    # Count query
    count_result = await db.execute(
        select(func.count(AuditLog.id)).where(and_(*conditions))
    )
    total_count = count_result.scalar()
    
    # Data query with pagination
    offset = (filters.page - 1) * filters.page_size
    result = await db.execute(
        select(AuditLog)
        .where(and_(*conditions))
        .order_by(desc(AuditLog.created_at))
        .offset(offset)
        .limit(filters.page_size)
    )
    logs = result.scalars().all()
    
    return list(logs), total_count
