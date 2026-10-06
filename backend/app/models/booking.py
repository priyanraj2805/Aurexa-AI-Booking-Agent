import uuid
from sqlalchemy import (
    Column,
    ForeignKey,
    DateTime,
    Boolean,
    String,
    Integer,
    Time,
    Date,
    Enum,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.base import Base
from app.models.config import MeetingLinkStatus, ActiveMeetingProvider
import enum


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    is_booked = Column(Boolean, default=False, nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    # Org-mode: NULL = individual/org-shared slot, set = that doctor's slot.
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        Index("ix_timeslot_availability", "org_id", "is_booked", "start_time"),
        # NOTE: uniqueness is enforced by partial indexes in org_mode_001
        # (individual: unique org+start where provider NULL;
        #  provider: unique org+provider+start) so two doctors can share a time.
    )


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    slot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("time_slots.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    customer_name = Column(String(255), nullable=False)
    customer_email = Column(String(255), nullable=False)
    customer_phone = Column(String(50), nullable=True)
    google_event_id = Column(String(255), nullable=True)
    outlook_event_id = Column(String(255), nullable=True)
    user_timezone = Column(String(64), nullable=True)
    user_slot_start_at = Column(DateTime(timezone=True), nullable=True)
    user_slot_end_at = Column(DateTime(timezone=True), nullable=True)
    timezone_source = Column(String(20), nullable=True)
    org_timezone_snapshot = Column(String(64), nullable=True)
    org_slot_start_at = Column(DateTime(timezone=True), nullable=True)
    org_slot_end_at = Column(DateTime(timezone=True), nullable=True)

    # Meeting link snapshot fields (Phase 16)
    meeting_type_snapshot = Column(String(20), nullable=True)  # ONLINE / OFFLINE
    meeting_link_url = Column(String(512), nullable=True)
    meeting_link_provider = Column(
        Enum(
            ActiveMeetingProvider, native_enum=False, name="meeting_link_provider_enum"
        ),
        nullable=True,
    )
    meeting_link_status = Column(
        Enum(MeetingLinkStatus, native_enum=False, name="meeting_link_status_enum"),
        default=MeetingLinkStatus.NOT_REQUIRED,
        server_default="NOT_REQUIRED",
        nullable=False,
    )
    meeting_link_external_id = Column(String(255), nullable=True)
    meeting_link_error_code = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # ── Payment snapshot (paid appointment) ─────────────────────────────
    payment_status = Column(String(20), nullable=False, default="not_required", server_default="not_required")  # not_required | pending | paid | failed | expired
    razorpay_order_id = Column(String(64), nullable=True, index=True)
    razorpay_payment_id = Column(String(64), nullable=True, index=True)
    amount_paise = Column(Integer, nullable=True)
    currency = Column(String(10), nullable=True)

    # Attendance tracking (online vs offline handled differently)
    attendance_status = Column(
        String(20),
        default="SCHEDULED",
        server_default="SCHEDULED",
        nullable=False,
    )  # SCHEDULED / ATTENDED / NO_SHOW / CANCELLED / RESCHEDULED
    attended_at = Column(DateTime(timezone=True), nullable=True)
    attendance_marked_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    attendance_notes = Column(String(500), nullable=True)
    rescheduled_from_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    cancellation_reason = Column(String(500), nullable=True)

    # ── Service catalog linkage (Phase: units/providers; engine wires later)
    unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        Index("ix_booking_org_created", "org_id", "created_at"),
        Index("ix_booking_attendance", "org_id", "attendance_status"),
    )


# ─── Virtual Slot Engine Models ────────────────────────────────────────────────


class ExceptionType(str, enum.Enum):
    CLOSED = "CLOSED"
    CUSTOM_HOURS = "CUSTOM_HOURS"


class BusinessSchedule(Base):
    """One row per org — defines slot duration and booking window."""

    __tablename__ = "business_schedules"

    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    slot_duration_minutes = Column(Integer, nullable=False, default=60)
    booking_window_days = Column(Integer, nullable=False, default=60)
    timezone = Column(String(64), nullable=False, default="UTC")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScheduleDay(Base):
    """Template for a day of week (0=Mon … 6=Sun). One row per weekday per org."""

    __tablename__ = "schedule_days"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL = org template hours, set = this doctor's override hours
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    day_of_week = Column(Integer, nullable=False)  # 0=Mon, 6=Sun
    is_active = Column(Boolean, nullable=False, default=True)
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)
    break_start = Column(Time, nullable=True)
    break_end = Column(Time, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "day_of_week", name="uq_schedule_org_day"),
    )


class ScheduleException(Base):
    """Per-date override — marks a specific calendar day as CLOSED or with custom hours."""

    __tablename__ = "schedule_exceptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    exception_date = Column(Date, nullable=False)
    # NULL = org-wide closed/custom day, set = only this doctor affected
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    exception_type = Column(
        Enum(ExceptionType, name="exception_type_enum"),
        nullable=False,
    )
    custom_open = Column(Time, nullable=True)  # used when CUSTOM_HOURS
    custom_close = Column(Time, nullable=True)  # used when CUSTOM_HOURS
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("org_id", "exception_date", name="uq_exception_org_date"),
        Index("ix_exception_org_date", "org_id", "exception_date"),
    )
