"""
WhatsApp campaign broadcast.

The engagement analytics (leads, waitlist, opt-outs, booking funnel) were
removed from the dashboard; only the campaign broadcast endpoint is left.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["whatsapp-campaign"])


class CampaignRequest(BaseModel):
    template_name: str
    body_params: List[str] = []
    limit: int = 500


@router.post("/whatsapp/campaign")
async def send_whatsapp_campaign(
    payload: CampaignRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send an APPROVED template to customers who messaged this org.

    Opt-outs are skipped automatically. The template must exist and be
    approved in the Meta template dashboard — otherwise every send fails
    and is reported.
    """
    from app.services import whatsapp_lifecycle as _life

    if not (payload.template_name or "").strip():
        raise HTTPException(status_code=422, detail="template_name is required")
    result = await _life.send_campaign(
        db,
        user.org_id,
        template_name=payload.template_name.strip(),
        body_params=list(payload.body_params or []),
        limit=max(1, min(int(payload.limit or 500), 2000)),
    )
    return result
