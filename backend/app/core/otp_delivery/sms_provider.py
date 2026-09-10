"""SMS OTP provider — thin wrapper around existing sms.py (Zavu). Keeps SMS intact."""
from app.core.sms import send_phone_otp_sms

class SMSProvider:
    async def send_otp(self, to: str, code: str, session_id: str) -> dict:
        # Reuse existing Zavu implementation (handles mock, test key, Plivo fallback)
        return await send_phone_otp_sms(to, code)
