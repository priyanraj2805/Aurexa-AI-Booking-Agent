import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        r=await db.execute(st("SELECT id, channel, channel_user_id, state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' ORDER BY updated_at DESC LIMIT 10"))
        for row in r.fetchall():
            print(f"id={str(row[0])[:8]} chan={row[1]} phone={row[2]} missing={row[4]}")
            sd=row[3] or {}
            print(f"  state_data keys={list(sd.keys())[:10]}")
            print(f"  name={sd.get('name')} email={sd.get('email')} phone={sd.get('phone')} date={sd.get('date')} time={sd.get('time')} active={str(sd.get('active_directive_id'))[:8] if sd.get('active_directive_id') else None}")
        print("--- directives ---")
        r2=await db.execute(st("SELECT customer_id, type, status, title, options FROM booking_directives ORDER BY created_at DESC LIMIT 10"))
        for row in r2.fetchall():
            print(f"dir cust={row[0]} type={row[1]} status={row[2]} title={row[3]} opts={len(row[4]) if row[4] else 0}")
            if row[4]:
                for o in row[4][:2]:
                    print(f"   {o}")
asyncio.run(run())
