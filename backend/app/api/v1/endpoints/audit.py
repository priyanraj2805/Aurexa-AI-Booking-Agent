from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.crud.audit import get_audit_logs
from app.db.database import get_db
from app.models.user import User
from app.schemas.audit import AuditLogFilter, AuditLogResponse

router = APIRouter()


@router.get("/logs", response_model=list[AuditLogResponse])
async def list_audit_logs(
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    action: Optional[str] = Query(default=None),
    actor_email: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filters = AuditLogFilter(
        start_date=start_date,
        end_date=end_date,
        action=action,
        actor_email=actor_email,
        resource_type=resource_type,
        page=page,
        page_size=page_size,
    )
    logs, _total = await get_audit_logs(db, user.org_id, filters)
    return [AuditLogResponse.model_validate(log) for log in logs]

