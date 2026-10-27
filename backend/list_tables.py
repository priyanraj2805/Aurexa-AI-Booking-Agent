import asyncio, os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
from app.db.database import engine
from sqlalchemy import text

async def main():
    async with engine.connect() as c:
        r = await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))
        print([row[0] for row in r.fetchall()])
    await engine.dispose()

asyncio.run(main())
