import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

async def test():
    async with AsyncSessionLocal() as db:
        res = await db.execute(st("SELECT id, type, status, title, options FROM booking_directives WHERE customer_id='919999999978' ORDER BY created_at DESC LIMIT 5"))
        for row in res.fetchall():
            print(f'ID: {row[0]} Type: {row[1]} Status: {row[2]} Title: {row[3]} Options: {len(row[4]) if row[4] else 0}')
            if row[4]:
                for o in row[4][:3]:
                    print(f'  {o}')

asyncio.run(test())