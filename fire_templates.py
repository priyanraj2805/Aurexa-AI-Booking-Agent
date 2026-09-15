import asyncio
from app.core import whatsapp_templates as wt

async def run():
    for fn, args in [
        (wt.send_welcome, ("919999999979", "there", "Dotstark Technologies")),
        (wt.send_request_email, ("919999999979", "there")),
        (wt.send_request_phone, ("919999999979",)),
    ]:
        try:
            r = await fn(*args)
            print(fn.__name__, "success=", r.get("success"), "wamid=", r.get("wamid"), "err=", str(r.get("error"))[:300])
        except Exception as e:
            print(fn.__name__, "EXC", str(e)[:300])

asyncio.run(run())
