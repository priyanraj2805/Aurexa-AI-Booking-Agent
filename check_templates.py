import asyncio, os
from app.core import whatsapp_templates as wt

async def run():
    print("enabled:", wt.templates_enabled())
    print("lang:", wt.template_language())
    print("names:", wt.TEMPLATE_WELCOME, wt.TEMPLATE_REQUEST_EMAIL, wt.TEMPLATE_REQUEST_PHONE)
    # Dry-run payload build (no network): check creds present without sending
    from app.core.config import settings
    pid = (getattr(settings, "WHATSAPP_OTP_PHONE_NUMBER_ID", "") or "")
    tok = (getattr(settings, "WHATSAPP_OTP_ACCESS_TOKEN", "") or "")
    print("cloud_creds_present:", bool(pid and tok), "pid_len:", len(pid))
    # Real send to a test number would deliver a live template — skip by default.
    # Uncomment to live-fire once Meta approved:
    # r = await wt.send_welcome("919999999979", "there", "Dotstark Technologies")
    # print("welcome:", r.get("success"), r.get("wamid"), str(r.get("error"))[:200])

asyncio.run(run())
