import asyncio
import logging
import uuid
import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import engine, AsyncSessionLocal
from app.core.config import settings

logger = logging.getLogger(__name__)


async def run_migrations():
    """Run alembic migrations."""
    import subprocess
    import sys
    import os

    # Alembic's default alembic_version.version_num is VARCHAR(32), but this
    # project's revision IDs are up to 34 chars (e.g. superadmin_phase8_*).
    # Fresh databases get the default 32-char column from Alembic and then
    # fail on the first long revision ID. Pre-create/widen the table so the
    # whole chain can run. Idempotent: harmless on existing 64-char tables.
    from sqlalchemy.ext.asyncio import create_async_engine

    fix_engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI)
    async with fix_engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(64) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
                """
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(64)"
            )
        )
    await fix_engine.dispose()

    logger.info("Running database migrations...")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "heads"],
        check=True,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env={**os.environ, "DATABASE_URL": settings.SQLALCHEMY_DATABASE_URI},
    )
    logger.info("Migrations completed successfully")


async def seed_default_organization(db: AsyncSession):
    """Create default DotStark Technologies organization with all configurations."""

    # Check if any organizations exist
    result = await db.execute(text("SELECT COUNT(*) FROM organizations"))
    org_count = result.scalar()

    if org_count > 0:
        logger.info(f"Database already has {org_count} organization(s), skipping seed")
        result = await db.execute(
            text("SELECT id FROM organizations ORDER BY created_at DESC LIMIT 1")
        )
        latest_org = result.fetchone()
        if latest_org:
            return str(latest_org[0])

    # Create organization
    org_id = uuid.UUID(
        "7e6fd64c-5e8e-4b32-9c20-8ca1a28751c6"
    )  # Use fixed ID for consistency
    api_key = "ds_yp7GG8AOp0Qtxzue4QDEu7We9LZohfMs"

    await db.execute(
        text("""
            INSERT INTO organizations (id, name, api_key, is_active, created_at)
            VALUES (:id, :name, :api_key, true, :created_at)
        """),
        {
            "id": org_id,
            "name": "DotStark Technologies",
            "api_key": api_key,
            "created_at": datetime.utcnow(),
        },
    )

    # Create Admin 1
    admin1_id = uuid.UUID("11111111-1111-4111-a111-111111111111")
    from passlib.hash import bcrypt

    hashed_password = bcrypt.hash("Dotstark@2026")

    await db.execute(
        text("""
            INSERT INTO users (id, org_id, email, full_name, hashed_password, is_active, created_at)
            VALUES (:id, :org_id, :email, :full_name, :password, true, :created_at)
        """),
        {
            "id": admin1_id,
            "org_id": org_id,
            "email": "admin1@dotstark.com",
            "full_name": "Admin 1",
            "password": hashed_password,
            "created_at": datetime.utcnow(),
        },
    )

    # Create Admin 2
    admin2_id = uuid.UUID("22222222-2222-4222-a222-222222222222")
    await db.execute(
        text("""
            INSERT INTO users (id, org_id, email, full_name, hashed_password, is_active, created_at)
            VALUES (:id, :org_id, :email, :full_name, :password, true, :created_at)
        """),
        {
            "id": admin2_id,
            "org_id": org_id,
            "email": "admin2@dotstark.com",
            "full_name": "Admin 2",
            "password": hashed_password,
            "created_at": datetime.utcnow(),
        },
    )

    # Create org config with exact settings
    required_fields = json.dumps(["name", "email", "organisation_name", "date", "time"])
    custom_prompts = json.dumps(
        {
            "tone": "professional",
            "agent_name": "Booking Agent",
            "greeting_message": "Hi There, I'm a Booking  Agent from DotStark Technologies, how may i help you today?",
            "industry_context": "IT Consultancy",
            "custom_instructions": "",
        }
    )
    field_definitions = json.dumps(
        [
            {
                "icon": "👤",
                "name": "name",
                "type": "text",
                "label": "Full Name",
                "order": 0,
                "options": [],
                "required": True,
                "description": "",
                "placeholder": "Your full name",
            },
            {
                "icon": "📧",
                "name": "email",
                "type": "email",
                "label": "Email Address",
                "order": 1,
                "options": [],
                "required": True,
                "description": "",
                "placeholder": "you@example.com",
            },
            {
                "icon": "🏢",
                "name": "organisation_name",
                "type": "text",
                "label": "Organisation Name",
                "order": 2,
                "options": [],
                "required": True,
                "description": "The User's Organisation Name",
                "placeholder": "Please Enter Your Organisation's Name",
            },
            {
                "icon": "📅",
                "name": "date",
                "type": "date",
                "label": "Preferred Date",
                "order": 3,
                "options": [],
                "required": True,
                "description": "",
                "placeholder": "Select a date",
            },
            {
                "icon": "⏰",
                "name": "time",
                "type": "time",
                "label": "Preferred Time",
                "order": 4,
                "options": [],
                "required": True,
                "description": "",
                "placeholder": "Select a time slot",
            },
        ]
    )

    await db.execute(
        text("""
            INSERT INTO org_configs (
                org_id, required_fields, custom_prompts, field_definitions, timezone,
                ui_brand_color, require_otp, max_booking_days_ahead,
                google_calendar_enabled, google_calendar_connected, active_calendar_provider,
                meeting_mode, allow_hybrid_selection, meeting_provider_default,
                google_meet_connected, active_meeting_provider, widget_token
            ) VALUES (
                :org_id, :required_fields, :custom_prompts, :field_definitions, :timezone,
                :ui_brand_color, :require_otp, :max_booking_days_ahead,
                :google_calendar_enabled, :google_calendar_connected, :active_calendar_provider,
                :meeting_mode, :allow_hybrid_selection, :meeting_provider_default,
                :google_meet_connected, :active_meeting_provider, :widget_token
            )
        """),
        {
            "org_id": org_id,
            "required_fields": required_fields,
            "custom_prompts": custom_prompts,
            "field_definitions": field_definitions,
            "timezone": "Asia/Kolkata",
            "ui_brand_color": "#3B82F6",
            "require_otp": False,
            "max_booking_days_ahead": 90,
            "google_calendar_enabled": True,
            "google_calendar_connected": True,
            "active_calendar_provider": "GOOGLE",
            "meeting_mode": "ONLINE_ONLY",
            "allow_hybrid_selection": False,
            "meeting_provider_default": "GOOGLE_MEET",
            "google_meet_connected": True,
            "active_meeting_provider": "GOOGLE_MEET",
            "widget_token": "ds_wid_uDy9_VGnwG5YAr3-WUKNH_CKXROWh43j",
        },
    )

    # Create business schedule
    await db.execute(
        text("""
            INSERT INTO business_schedules (org_id, slot_duration_minutes, booking_window_days, timezone, created_at, updated_at)
            VALUES (:org_id, :slot_duration, :booking_window, :timezone, :created_at, :created_at)
        """),
        {
            "org_id": org_id,
            "slot_duration": 30,
            "booking_window": 90,
            "timezone": "Asia/Kolkata",
            "created_at": datetime.utcnow(),
        },
    )

    await db.commit()
    logger.info(f"Created default organization: DotStark Technologies (ID: {org_id})")
    logger.info(f"API Key: {api_key}")
    logger.info(f"Widget Token: ds_wid_uDy9_VGnwG5YAr3-WUKNH_CKXROWh43j")
    logger.info(f"User Email: devang.dotstark@outlook.com")
    logger.info(f"User Password: Dotstark@2026")

    return str(org_id)


async def initialize_database():
    """Initialize database: run migrations and seed default data."""
    logger.info("Starting database initialization...")

    # Always run migrations so existing databases pick up new columns.
    await run_migrations()

    # Seed only when no organizations exist.
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM organizations"))
        org_count = result.scalar() or 0
        if org_count > 0:
            logger.info(f"Database already initialized with {org_count} organization(s)")
            return
        await seed_default_organization(session)

    logger.info("Database initialization complete!")


if __name__ == "__main__":
    asyncio.run(initialize_database())
