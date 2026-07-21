from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
import logging

logger = logging.getLogger(__name__)

@tool
async def trigger_otp(channel: str, config: RunnableConfig) -> str:
    """
    Triggers an OTP (One-Time Password) to the user's provided email address.
    Must ONLY be called after the user's email has been saved via save_field_value 
    and the user has confirmed they want to proceed with the booking.
    
    Args:
        channel: The channel to send OTP to, usually 'email'.
    """
    db = config.get("configurable", {}).get("db")
    session_id = config.get("configurable", {}).get("thread_id")
    org_id_str = config.get("configurable", {}).get("org_id")
    db_lock = config.get("configurable", {}).get("db_lock")
    
    if not db or not session_id or not org_id_str or not db_lock:
        return "System error: Missing required context to trigger OTP."

    from app.crud.chat import get_chat_session
    from app.crud.otp import generate_otp
    from app.core.mail import send_otp_email

    try:
        async with db_lock:
            chat_session = await get_chat_session(db, session_id)
            if not chat_session:
                return "System error: Chat session not found."

            email = chat_session.collected_data.get("email") if isinstance(chat_session.collected_data, dict) else None
            if not email:
                return "Error: Cannot send OTP because no email has been collected yet."

            code = await generate_otp(db, session_id)
        
        # In a real tool workflow, we await the email dispatch (can be outside lock)
        await send_otp_email(
            to_email=email,
            code=code,
            org_id=org_id_str,
        )
        return f"SUCCESS: OTP has been sent via {channel} to {email}."
    except Exception as e:
        logger.error(f"Failed to trigger OTP: {e}")
        return f"System error: Failed to trigger OTP - {str(e)}."

@tool
async def verify_otp(code: str, config: RunnableConfig) -> str:
    """
    Verifies the OTP (One-Time Password) that the user provides.
    Call this tool when the user inputs a 6-digit code.
    
    Args:
        code: The 6-digit code provided by the user.
    """
    db = config.get("configurable", {}).get("db")
    session_id = config.get("configurable", {}).get("thread_id")
    db_lock = config.get("configurable", {}).get("db_lock")
    
    if not db or not session_id or not db_lock:
        return "System error: Missing required context to verify OTP."

    from app.crud.otp import verify_otp as crud_verify_otp

    try:
        async with db_lock:
            success = await crud_verify_otp(db, session_id, code)
        if success:
            return "SUCCESS: OTP verified. You may now proceed with creating the booking."
        else:
            return "FAILURE: Invalid or expired OTP. Please ask the user to try again or request a new one."
    except ValueError as ve:
        return f"FAILURE: {str(ve)}"
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        return "System error: Failed to verify OTP."
