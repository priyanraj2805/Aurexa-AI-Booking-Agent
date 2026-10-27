import asyncio, os, sys, importlib, pkgutil
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
from app.db.database import engine
from app.db.base import Base
import app.models
for m in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{m.name}")

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.tables["providers"].create(c, checkfirst=True))
    print("created providers", flush=True)
    await engine.dispose()

asyncio.run(main())
