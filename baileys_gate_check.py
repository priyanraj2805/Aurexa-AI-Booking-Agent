import asyncio, os, httpx, uuid, time
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

PHONE = "919999999974"
CONN = "8667fc9f-7338-4a6e-ba1b-1b4b1ebf4854"

async def send(txt):
    key = os.getenv("WA_GATEWAY_SERVICE_KEY", "")
    base = "http://localhost:8005/api/v1/internal/whatsapp-qr/inbound"
    p = {"connectionId": CONN, "messageId": str(uuid.uuid4()), "from": PHONE,
         "chatJid": f"{PHONE}@s.whatsapp.net", "text": txt, "timestamp": int(time.time())}
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(base, headers={"Content-Type": "application/json", "X-Gateway-Key": key}, json=p)
        d = r.json().get("reply", {})
        print(f"sent {txt!r} -> {d.get('type')} {str(d.get('body',''))[:80]}")

async def run():
    async with AsyncSessionLocal() as db:
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id='919999999974'"))
        await db.execute(st("DELETE FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id='919999999974'"))
        await db.commit()
    await send("Hi")
    await send("Aarav Sharma")
    await send("baileysreg@example.com")
    await send("9876543210")
    async with AsyncSessionLocal() as db:
        r = await db.execute(st(
            "SELECT type, status FROM booking_directives WHERE customer_id='919999999974'"))
        rows = r.fetchall()
        print("baileys directives:", [(x[0], x[1]) for x in rows])
        print("BAILEYS_GATE_REGRESSION:", "PASS" if any(t == "SELECT_DATE" and s == "PENDING" for t, s in rows) else "FAIL")
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id='919999999974'"))
        await db.execute(st("DELETE FROM chat_sessions WHERE channel_user_id='919999999974'"))
        await db.commit()
        print("cleaned")

asyncio.run(run())
