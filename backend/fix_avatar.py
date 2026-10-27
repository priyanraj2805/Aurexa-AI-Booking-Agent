import asyncio, os
from dotenv import load_dotenv
load_dotenv("backend/.env")
import asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://postgres:dotstark_secure_pass@localhost:5432/dotstark_v2")
    await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)")
    print("done")
    await conn.close()

asyncio.run(main())
