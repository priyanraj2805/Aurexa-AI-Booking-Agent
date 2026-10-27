import asyncio, sys, traceback, os
sys.path.insert(0, "backend")
os.environ["PYTHONPATH"] = "backend"
from dotenv import load_dotenv
load_dotenv("backend/.env")
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.core.config import settings

print("DB URI:", settings.SQLALCHEMY_DATABASE_URI)

async def test():
    try:
        from app.models.user import User
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).limit(1))
            user = result.scalars().first()
            print("DB QUERY SUCCESS:", user)
    except Exception as e:
        print("DB QUERY FAILED:")
        print(str(e))
        traceback.print_exc()

asyncio.run(test())
