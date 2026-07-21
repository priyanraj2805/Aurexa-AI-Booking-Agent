from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text
from datetime import datetime, timedelta, date, time, timezone
from typing import Optional, List
from pydantic import BaseModel, validator
import uuid
import logging

from app.db.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


class BulkSlotRequest(BaseModel):
    days_ahead: int = 90
    start_hour: int = 10
    end_hour: int = 18
    slot_duration_minutes: int = 60
    skip_weekends: bool = True
    buffer_percentage: float = 0.2

    @validator("start_hour")
    def validate_start_hour(cls, v):
        if not (0 <= v <= 23):
            raise ValueError("start_hour must be between 0 and 23")
        return v

    @validator("end_hour")
    def validate_end_hour(cls, v):
        if not (0 <= v <= 23):
            raise ValueError("end_hour must be between 0 and 23")
        return v

    @validator("slot_duration_minutes")
    def validate_slot_duration(cls, v):
        if v <= 0:
            raise ValueError("slot_duration_minutes must be greater than 0")
        return v

    @validator("days_ahead")
    def validate_days_ahead(cls, v):
        if v <= 0:
            raise ValueError("days_ahead must be greater than 0")
        if v > 365:
            raise ValueError("days_ahead must not exceed 365")
        return v

    @validator("buffer_percentage")
    def validate_buffer_percentage(cls, v):
        if not (0.0 <= v <= 1.0):
            raise ValueError("buffer_percentage must be between 0.0 and 1.0")
        return v

    @validator("end_hour")
    def validate_start_end_hours(cls, v, values):
        if "start_hour" in values and v <= values["start_hour"]:
            raise ValueError("end_hour must be greater than start_hour")
        return v


class BulkSlotResponse(BaseModel):
    message: str
    slots_created: int


@router.post("/dashboard/availability/bulk", response_model=BulkSlotResponse)
async def create_bulk_slots(
    request: BulkSlotRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk create time slots for the organization.
    """
    if not user.org_id:
        raise HTTPException(
            status_code=400, detail="User does not belong to an organization"
        )

    import random

    start_date = date.today() + timedelta(days=1)
    slots_created = 0
    slot_data = []

    for day_offset in range(request.days_ahead):
        current_date = start_date + timedelta(days=day_offset)

        # Skip weekends if requested
        if request.skip_weekends and current_date.weekday() >= 5:
            continue

        # Create slots for the day
        current_dt = datetime.combine(
            current_date, time(request.start_hour, 0)
        ).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(current_date, time(request.end_hour, 0)).replace(
            tzinfo=timezone.utc
        )

        while current_dt < end_dt:
            next_dt = current_dt + timedelta(minutes=request.slot_duration_minutes)

            if next_dt > end_dt:
                break

            # Randomly skip some slots to create realistic availability (buffer_percentage)
            if random.random() >= request.buffer_percentage:
                # 10% chance the slot is already booked
                is_booked = random.random() < 0.1

                slot_data.append(
                    {
                        "id": str(uuid.uuid4()),
                        "org_id": user.org_id,
                        "start": current_dt,
                        "end": next_dt,
                        "booked": is_booked,
                    }
                )

            current_dt = next_dt

    # Batch insert all slots
    try:
        for slot in slot_data:
            result = await db.execute(
                text("""
                INSERT INTO time_slots (id, org_id, start_time, end_time, is_booked) 
                VALUES (:id, :org_id, :start, :end, :booked) 
                ON CONFLICT ON CONSTRAINT uq_timeslot_org_start DO NOTHING
                RETURNING id
                """),
                slot,
            )
            if result.fetchone():
                slots_created += 1
        await db.commit()
    except Exception as e:
        logger.error(f"Error creating slots: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create slots")

    return BulkSlotResponse(
        message=f"Successfully generated {slots_created} slots",
        slots_created=slots_created,
    )
