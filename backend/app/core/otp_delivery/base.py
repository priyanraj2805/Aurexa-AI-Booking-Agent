"""OTP delivery abstraction — keep SMS intact, add WhatsApp beside it."""
from typing import Protocol, Dict

class OTPDeliveryProvider(Protocol):
    async def send_otp(self, to: str, code: str, session_id: str) -> Dict:
        """Send OTP code to `to` (E.164). Return {success: bool, ...} matching sms.py shape."""
        ...
