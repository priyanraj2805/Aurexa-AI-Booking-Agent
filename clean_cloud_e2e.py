import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

async def run():
    async with AsyncSessionLocal() as db:
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id='919999999973'"))
        await db.execute(st("DELETE FROM chat_sessions WHERE channel_user_id='919999999973'"))
        await db.commit()
        print("cleaned 919999999973")

asyncio.run(run())
