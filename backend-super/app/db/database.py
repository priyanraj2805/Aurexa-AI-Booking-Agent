from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Admin pool — used ONLY for the super_admin schema (auth, MFA, RBAC).
# It uses the postgres admin credentials and must never touch tenant tables.
engine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    echo=False,
)

# Read-only pool — used for ALL tenant data reads (organizations, users,
# bookings, conversations, usage). Bound to super_admin_readonly which has
# SELECT-only grants + default_transaction_read_only=on, so a bug here can
# never corrupt tenant data.
readonly_engine = create_async_engine(
    settings.SUPER_ADMIN_READONLY_DATABASE_URI,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    echo=False,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

ReadonlySessionLocal = sessionmaker(
    bind=readonly_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Session bound to the super_admin schema pool."""
    async with AsyncSessionLocal() as session:
        yield session


async def get_readonly_db() -> AsyncGenerator[AsyncSession, None]:
    """Session bound to the read-only tenant pool."""
    async with ReadonlySessionLocal() as session:
        yield session
