import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

async def run():
    async with AsyncSessionLocal() as db:
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id IN ('919999999971','919999999979')"))
        await db.execute(st("DELETE FROM chat_sessions WHERE channel_user_id IN ('919999999971','919999999979')"))
        await db.commit()
        print("test artifacts cleaned")

asyncio.run(run())
