# ── Onboarding ──────────────────────────────────────────────────────

class OnboardingBusinessInfo(BaseModel):
    industry_type: str = Field(..., min_length=1, max_length=100)


class OnboardingHoursDay(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)  # 0=Mon, 6=Sun
    is_active: bool = True
    open_time: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    close_time: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    break_start: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    break_end: Optional[str] = Field(None, pattern=r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


class OnboardingHours(BaseModel):
    timezone: str = Field(..., min_length=1)
    slot_duration_minutes: int = Field(..., ge=15, le=120)
    booking_window_days: int = Field(..., ge=1, le=365)
    weekly_schedule: List[OnboardingHoursDay] = Field(..., min_length=7, max_length=7)


class OnboardingAgent(BaseModel):
    agent_name: str = Field(default="Booking Assistant", max_length=100)
    greeting_message: str = Field(default="Hello! How can I help you book today?", max_length=500)
    tone: str = Field(default="friendly and professional", max_length=50)
    require_otp: bool = True


class OnboardingRequest(BaseModel):
    business: OnboardingBusinessInfo
    hours: OnboardingHours
    agent: OnboardingAgent


class OnboardingResponse(BaseModel):
    success: bool
    message: str


class OnboardingStatusResponse(BaseModel):
    onboarding_completed: bool
    industry_type: Optional[str] = None
    hours_configured: bool = False


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if onboarding is completed for the current user's org."""
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    hours_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
    )
    schedule = hours_result.scalars().first()

    return OnboardingStatusResponse(
        onboarding_completed=org.onboarding_completed,
        industry_type=org.industry_type,
        hours_configured=schedule is not None,
    )


@router.post("/onboarding", response_model=OnboardingResponse)
async def complete_onboarding(
    data: OnboardingRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Complete the mandatory onboarding flow for a new organization."""
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Upsert business info
    org.industry_type = data.business.industry_type
    org.onboarding_completed = True
    org.timezone = data.hours.timezone

    # Upsert BusinessSchedule
    schedule_result = await db.execute(
        select(BusinessSchedule).where(BusinessSchedule.org_id == org.id)
    )
    schedule = schedule_result.scalars().first()
    if not schedule:
        schedule = BusinessSchedule(org_id=org.id)
        db.add(schedule)

    schedule.timezone = data.hours.timezone
    schedule.slot_duration_minutes = data.hours.slot_duration_minutes
    schedule.booking_window_days = data.hours.booking_window_days

    # Upsert ScheduleDays
    # Delete existing days and recreate
    await db.execute(
        ScheduleDay.__table__.delete().where(ScheduleDay.org_id == org.id)
    )
    for day_data in data.hours.weekly_schedule:
        day = ScheduleDay(
            org_id=org.id,
            day_of_week=day_data.day_of_week,
            is_active=day_data.is_active,
            open_time=day_data.open_time,
            close_time=day_data.close_time,
            break_start=day_data.break_start,
            break_end=day_data.break_end,
        )
        db.add(day)

    # Update agent persona
    persona = dict(org.custom_prompts) if isinstance(org.custom_prompts, dict) else {}
    persona.update({
        "agent_name": data.agent.agent_name,
        "greeting_message": data.agent.greeting_message,
        "tone": data.agent.tone,
    })
    org.custom_prompts = persona

    # Update org config fields
    org.require_otp = data.agent.require_otp
    org.max_booking_days_ahead = data.hours.booking_window_days

    await db.commit()

    await log_audit_event(
        db=db,
        org_id=user.org_id,
        actor_user_id=user.id,
        action=AuditAction.ORG_SETTINGS_UPDATE,
        resource_type="organization",
        resource_id=str(user.org_id),
        metadata={"action": "onboarding_complete"},
    )

    return OnboardingResponse(success=True, message="Onboarding completed successfully")


@router.get("/onboarding/hours-template", response_model=List[OnboardingHoursDay])
async def get_hours_template(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a default weekly hours template (Mon-Fri 9-5, weekends closed)."""
    return [
        OnboardingHoursDay(day_of_week=i, is_active=i < 5, open_time="09:00", close_time="17:00")
        for i in range(7)
    ]