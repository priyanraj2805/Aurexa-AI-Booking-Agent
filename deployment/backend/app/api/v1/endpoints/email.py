from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.crud.email_delivery import get_email_history
from app.db.database import get_db
from app.models.user import User
from app.schemas.email_delivery import (
    EmailChannel,
    EmailDeliveryStatus,
    EmailHistoryFilter,
    EmailHistoryResponse,
)

router = APIRouter()


@router.get("/history", response_model=list[EmailHistoryResponse])
async def list_email_history(
    status: Optional[EmailDeliveryStatus] = Query(default=None),
    channel: Optional[EmailChannel] = Query(default=None),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filters = EmailHistoryFilter(
        status=status,
        channel=channel,
        from_date=from_date,
        to_date=to_date,
        skip=skip,
        limit=limit,
    )
    rows, _total = await get_email_history(db, user.org_id, filters)
    return [EmailHistoryResponse.model_validate(row) for row in rows]

