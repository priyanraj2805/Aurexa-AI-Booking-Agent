import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        r=await db.execute(st("SELECT channel, channel_user_id, collected_data, missing_fields, state_data FROM chat_sessions ORDER BY created_at DESC LIMIT 15"))
        for row in r.fetchall():
            print(f"{row[0]} {row[1]} missing={row[3]} collected={row[2]} state_data={row[4]}")
        print("--- directives ---")
        r2=await db.execute(st("SELECT customer_id, type, status, title, options FROM booking_directives ORDER BY created_at DESC LIMIT 10"))
        for row in r2.fetchall():
            print(f"dir {row[0]} {row[1]} {row[2]} {row[3]} opts={len(row[4]) if row[4] else 0}")
asyncio.run(run())
