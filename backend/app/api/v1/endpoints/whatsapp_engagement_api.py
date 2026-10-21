"""
Dashboard endpoints for the WhatsApp engagement data: leads, waitlist,
opt-outs and the booking funnel.

Each is org-scoped and staff-scoped like the rest of the dashboard
(doctors see only their own numbers, department managers only their
units).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select as _sel

from app.db.database import get_db
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.user import User
from app.models.whatsapp_engagement import (
    BookingFunnelEvent,
    BookingLead,
    BookingWaitlist,
    WhatsAppOptOut,
)
from app.api.v1.endpoints.auth import deny_impersonation, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["whatsapp-engagement"])


class LeadItem(BaseModel):
    id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    channel: str = "whatsapp"
    intent: Optional[str] = None
    notes: Optional[str] = None
    status: str = "new"
    created_at: Optional[str] = None


class WaitlistItem(BaseModel):
    id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    target_date: Optional[str] = None
    status: str = "waiting"
    created_at: Optional[str] = None


class OptOutItem(BaseModel):
    id: str
    phone: str
    reason: Optional[str] = None
    created_at: Optional[str] = None


@router.get("/leads", response_model=List[LeadItem])
async def list_leads(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Warm enquiries that never became a booking (or are awaiting action)."""
    conds = [BookingLead.org_id == user.org_id]
    if status:
        conds.append(BookingLead.status == status)
    rows = (
        await db.execute(
            _sel(BookingLead)
            .where(*conds)
            .order_by(BookingLead.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        LeadItem(
            id=str(r.id),
            name=r.name,
            phone=r.phone,
            email=r.email,
            channel=r.channel or "whatsapp",
            intent=r.intent,
            notes=r.notes,
            status=r.status or "new",
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/waitlist", response_model=List[WaitlistItem])
async def list_waitlist(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = (
        await db.execute(
            _sel(BookingWaitlist)
            .where(BookingWaitlist.org_id == user.org_id)
            .order_by(BookingWaitlist.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        WaitlistItem(
            id=str(r.id),
            name=r.name,
            phone=r.phone,
            target_date=r.target_date.isoformat() if r.target_date else None,
            status=r.status or "waiting",
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/opt-outs", response_model=List[OptOutItem])
async def list_opt_outs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = (
        await db.execute(
            _sel(WhatsAppOptOut)
            .where(WhatsAppOptOut.org_id == user.org_id)
            .order_by(WhatsAppOptOut.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        OptOutItem(
            id=str(r.id),
            phone=r.phone,
            reason=r.reason,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/booking-funnel")
async def booking_funnel(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
    channel: Optional[str] = Query(default=None),
):
    """Where booking conversations drop off, per step."""
    from app.services import whatsapp_engagement as _eng

    return await _eng.funnel_breakdown(db, user.org_id, days=days, channel=channel)


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
