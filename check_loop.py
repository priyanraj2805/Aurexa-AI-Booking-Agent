import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

async def run():
    async with AsyncSessionLocal() as db:
        r = await db.execute(st(
            "SELECT channel, channel_user_id, missing_fields, state_data, updated_at "
            "FROM chat_sessions WHERE channel_user_id LIKE '%6350063338%' "
            "ORDER BY updated_at DESC LIMIT 4"))
        for row in r.fetchall():
            sd = row[3] or {}
            print(f"chan={row[0]} user={row[1]} missing={row[2]} updated={row[4]}")
            print(f"  name={sd.get('name')} email={sd.get('email')} phone={sd.get('phone')} "
                  f"date={sd.get('date')} time={sd.get('time')} active={str(sd.get('active_directive_id'))[:8] if sd.get('active_directive_id') else None}")

asyncio.run(run())
