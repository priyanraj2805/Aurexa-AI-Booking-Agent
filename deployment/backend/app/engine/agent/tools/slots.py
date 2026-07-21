import uuid
from typing import Optional, List, Dict
from datetime import date, datetime, timedelta, timezone

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from ...slot_engine import compute_available_slots


@tool
async def check_available_slots(target_date: str, config: RunnableConfig) -> str:
    """
    Check the database for available appointment slots on a specific date.
    Use this tool whenever a user asks about availability or wants to book on a specific day.
    
    Args:
        target_date: The date to check in YYYY-MM-DD format (e.g., '2026-04-14').
    """
    # 1. Extract context from config
    # The LangGraph execution config holds our DB session and org details
    db = config.get("configurable", {}).get("db")
    org_id_str = config.get("configurable", {}).get("org_id")
    org_config_dict = config.get("configurable", {}).get("org_config")
    user_offset_hours = config.get("configurable", {}).get("user_offset_hours", 0)
    db_lock = config.get("configurable", {}).get("db_lock")

    if not db or not org_id_str or not db_lock:
        return "System error: Could not access database context."

    try:
        org_id = uuid.UUID(org_id_str)
        search_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return f"Error: Invalid date format '{target_date}'. Must be YYYY-MM-DD."

    # Reconstruct the OrgConfig model to pass an object with .active_calendar_provider to the slot engine
    from app.models.config import OrgConfig
    org_config = OrgConfig(**org_config_dict) if org_config_dict else None

    # 2. Call the Slot Engine
    # Returns list of available UTC time objects
    async with db_lock:
        available_utc_times = await compute_available_slots(
            db=db,
            org_id=org_id,
            target_date=search_date,
            org_config=org_config
        )
    
    if not available_utc_times:
        return f"No available slots on {target_date}."

    # 3. Timezone conversion
    # We must convert the UTC times returned by slot engine to the user's local timezone
    # so the LLM speaks in local time rather than UTC.
    formatted_times = []
    for t_utc in available_utc_times:
        # Create a full datetime in UTC to apply timedelta cleanly
        dt_utc = datetime.combine(search_date, t_utc) 
        dt_local = dt_utc + timedelta(hours=user_offset_hours)
        
        # Format something like "10:00 AM"
        ampm = "AM" if dt_local.hour < 12 else "PM"
        hour_12 = dt_local.hour % 12 or 12
        formatted_times.append(f"{hour_12}:{dt_local.minute:02d} {ampm}")

    result = f"Available slots on {target_date} (Local Time):\n" + "\n".join(f"- {t}" for t in formatted_times)
    return result
