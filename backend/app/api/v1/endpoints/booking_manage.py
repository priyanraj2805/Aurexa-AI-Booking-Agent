"""
Public self-service booking management.

A signed link the customer can open from WhatsApp to view, reschedule or
cancel their booking — without typing natural language and without another
LLM turn. Token is an HMAC over (booking_id, expiry), verified before
anything is read.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select as _sel

from app.db.database import get_db
from app.models.booking import Booking
from app.models.session import ChatSession
from app.services import whatsapp_lifecycle as _life

logger = logging.getLogger(__name__)

router = APIRouter()


class BookingView(BaseModel):
    booking_id: str
    date: Optional[str] = None
    time: Optional[str] = None
    department: Optional[str] = None
    team_member: Optional[str] = None
    status: str = "SCHEDULED"
    meeting_link: Optional[str] = None
    can_manage: bool = True


class ManageResponse(BaseModel):
    ok: bool
    booking: Optional[BookingView] = None
    message: str = ""


async def _load_booking(db: AsyncSession, booking_id: str) -> Optional[Booking]:
    import uuid as _uuid

    try:
        bid = _uuid.UUID(str(booking_id))
    except Exception:
        return None
    return (
        await db.execute(
            _sel(Booking).where(Booking.id == bid)
        )
    ).scalars().first()


def _fmt(booking: Booking) -> Dict[str, Any]:
    start = getattr(booking, "user_slot_start_at", None) or getattr(
        booking, "org_slot_start_at", None
    )
    date_s = None
    time_s = None
    if start is not None:
        try:
            date_s = start.strftime("%Y-%m-%d")
            time_s = start.strftime("%I:%M %p")
        except Exception:
            pass
    return {
        "booking_id": str(booking.id),
        "date": date_s,
        "time": time_s,
        "status": str(getattr(booking, "attendance_status", "SCHEDULED") or "SCHEDULED"),
        "meeting_link": getattr(booking, "meeting_link_url", None),
    }


@router.get("/booking/manage", response_model=ManageResponse)
async def get_manageable_booking(
    booking_id: str = Query(...),
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """View a booking via its signed manage link."""
    if not _life.verify_manage_token(booking_id, token):
        raise HTTPException(status_code=403, detail="Invalid or expired link")
    booking = await _load_booking(db, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    view = _fmt(booking)
    # Enrich with department / member names when the catalog is used.
    try:
        from app.services.catalog_service import booking_email_context

        view.update(await booking_email_context(db, booking))
    except Exception:
        pass
    return ManageResponse(ok=True, booking=BookingView(**view), message="")


@router.post("/booking/manage/cancel", response_model=ManageResponse)
async def cancel_via_manage_link(
    booking_id: str = Query(...),
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a booking from the self-service link."""
    if not _life.verify_manage_token(booking_id, token):
        raise HTTPException(status_code=403, detail="Invalid or expired link")
    booking = await _load_booking(db, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if str(getattr(booking, "attendance_status", "") or "").upper() == "CANCELLED":
        return ManageResponse(
            ok=True, booking=BookingView(**_fmt(booking)),
            message="This booking is already cancelled.",
        )
    try:
        from app.crud.booking import cancel_booking

        cancelled = await cancel_booking(
            db, booking_id=str(booking.id), org_id=booking.org_id
        )
    except Exception as exc:
        logger.warning("manage-link cancel failed booking=%s: %s", booking_id, exc)
        raise HTTPException(status_code=500, detail="Could not cancel the booking")
    if not cancelled:
        raise HTTPException(status_code=409, detail="Could not cancel the booking")
    # Free the slot for someone on the waitlist.
    try:
        from app.services.whatsapp_lifecycle import notify_waitlist_on_slot_open

        await notify_waitlist_on_slot_open(db, booking.org_id)
    except Exception:
        pass
    # Notify the customer over WhatsApp when possible.
    try:
        from app.services import whatsapp_reminders as _rem

        await _rem.send_booking_cancelled_message(
            db, booking, business_name="Dotstark Technologies"
        )
    except Exception:
        pass
    return ManageResponse(
        ok=True, booking=BookingView(**_fmt(booking)),
        message="Your booking has been cancelled. Any paid amount is refunded to your original payment method.",
    )
