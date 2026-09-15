import asyncio, os, httpx, uuid, time
PHONE = "919999999971"
CONN = "8667fc9f-7338-4a6e-ba1b-1b4b1ebf4854"

async def send(txt):
    key = os.getenv("WA_GATEWAY_SERVICE_KEY", "")
    base = "http://localhost:8005/api/v1/internal/whatsapp-qr/inbound"
    p = {"connectionId": CONN, "messageId": str(uuid.uuid4()), "from": PHONE,
         "chatJid": f"{PHONE}@s.whatsapp.net", "text": txt, "timestamp": int(time.time())}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(base, headers={"Content-Type": "application/json", "X-Gateway-Key": key}, json=p)
        j = r.json()
        rp = j.get("reply") or {}
        print("reply_type=", rp.get("type"), "body=", str(rp.get("body", ""))[:120])

async def run():
    from app.db.database import AsyncSessionLocal
    from sqlalchemy import text as st
    async with AsyncSessionLocal() as db:
        await db.execute(st("DELETE FROM chat_sessions WHERE channel_user_id='919999999971'"))
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id='919999999971'"))
        await db.commit()
    await send("Hi")

asyncio.run(run())
