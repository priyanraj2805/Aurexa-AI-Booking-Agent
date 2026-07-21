from typing import AsyncGenerator
import logging
import time

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    echo=False
)

logger = logging.getLogger("app.db.slow_query")


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.perf_counter())


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    starts = conn.info.get("query_start_time") or []
    if not starts:
        return
    duration_ms = (time.perf_counter() - starts.pop()) * 1000
    if duration_ms > settings.DB_SLOW_QUERY_THRESHOLD:
        logger.warning(
            "slow_query",
            extra={
                "event": "slow_query",
                "duration_ms": round(duration_ms, 2),
                "statement": statement[:2000],
            },
        )

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
