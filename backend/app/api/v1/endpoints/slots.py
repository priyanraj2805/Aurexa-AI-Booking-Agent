from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.sql import func
from pydantic import BaseModel
from datetime import datetime
from typing import List
import uuid

from app.db.database import get_db
from app.models.organization import Organization
from app.models.booking import TimeSlot

router = APIRouter()

class TimeSlotCreate(BaseModel):
    start_time: datetime
    end_time: datetime

class TimeSlotResponse(BaseModel):
    id: str
    org_id: str
    start_time: datetime
    end_time: datetime
    is_booked: bool

async def get_current_org(x_org_api_key: str = Header(...), db: AsyncSession = Depends(get_db)) -> Organization:
    result = await db.execute(select(Organization).where(Organization.api_key == x_org_api_key))
    org = result.scalars().first()
    if not org or not org.is_active:
        raise HTTPException(status_code=403, detail="Invalid or inactive API Key")
    return org

@router.post("/", response_model=TimeSlotResponse, status_code=201)
async def create_slot(
    payload: TimeSlotCreate,
    org: Organization = Depends(get_current_org),
    db: AsyncSession = Depends(get_db)
):
    """Create a single available time slot for this organization."""
    slot = TimeSlot(
        org_id=org.id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        is_booked=False
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return TimeSlotResponse(
        id=str(slot.id),
        org_id=str(slot.org_id),
        start_time=slot.start_time,
        end_time=slot.end_time,
        is_booked=slot.is_booked
    )

@router.get("/", response_model=List[TimeSlotResponse])
async def list_slots(
    org: Organization = Depends(get_current_org),
    db: AsyncSession = Depends(get_db)
):
    """List all time slots for this organization."""
    result = await db.execute(
        select(TimeSlot)
        .where(TimeSlot.org_id == org.id)
        .order_by(TimeSlot.start_time)
    )
    slots = result.scalars().all()
    return [
        TimeSlotResponse(
            id=str(s.id),
            org_id=str(s.org_id),
            start_time=s.start_time,
            end_time=s.end_time,
            is_booked=s.is_booked
        )
        for s in slots
    ]

@router.delete("/{slot_id}", status_code=204)
async def delete_slot(
    slot_id: str,
    org: Organization = Depends(get_current_org),
    db: AsyncSession = Depends(get_db)
):
    """Delete a time slot."""
    result = await db.execute(select(TimeSlot).where(TimeSlot.id == slot_id, TimeSlot.org_id == org.id))
    slot = result.scalars().first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    await db.delete(slot)
    await db.commit()
