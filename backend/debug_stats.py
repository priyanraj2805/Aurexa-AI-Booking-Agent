import asyncio, os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
from app.db.database import AsyncSessionLocal
from app.models.user import User
from sqlalchemy import select
from app.api.v1.endpoints.dashboard import get_stats

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).limit(1))
        user = res.scalars().first()
        print("user:", user.id, user.email, flush=True)
        out = await get_stats(user=user, db=db, days=14)
        print("OK total_bookings:", out.total_bookings, flush=True)
        print("DONE", flush=True)

asyncio.run(main())
print("EXIT OK", flush=True)
