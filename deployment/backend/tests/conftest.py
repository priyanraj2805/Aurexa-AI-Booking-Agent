"""
Test fixtures — async SQLite engine, test client, and auth helpers.

Uses aiosqlite so tests run without a real PostgreSQL instance.
Every test gets a fresh database (tables are created/dropped per-session).

PostgreSQL-specific types (UUID, JSONB) are compiled to SQLite equivalents
and their bind/result processors are monkey-patched for full compatibility.
"""
import json as _json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ── Map PostgreSQL types to SQLite equivalents ────────────────────────
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB as PG_JSONB

@compiles(PG_UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(36)"

@compiles(PG_JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


# ── Monkey-patch bind/result processors for SQLite ────────────────────
# PostgreSQL UUID and JSONB types need custom processors when running
# against SQLite so that Python uuid objects and dicts serialize correctly.

_orig_uuid_bind = PG_UUID.bind_processor
_orig_uuid_result = PG_UUID.result_processor

def _uuid_bind_sqlite(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return value
            return str(value) if not isinstance(value, str) else value
        return process
    return _orig_uuid_bind(self, dialect)

def _uuid_result_sqlite(self, dialect, coltype):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return value
            if self.as_uuid:
                return uuid.UUID(value) if not isinstance(value, uuid.UUID) else value
            return value
        return process
    return _orig_uuid_result(self, dialect, coltype)

PG_UUID.bind_processor = _uuid_bind_sqlite
PG_UUID.result_processor = _uuid_result_sqlite

_orig_jsonb_bind = PG_JSONB.bind_processor
_orig_jsonb_result = PG_JSONB.result_processor

def _jsonb_bind_sqlite(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return value
            return _json.dumps(value)
        return process
    return _orig_jsonb_bind(self, dialect)

def _jsonb_result_sqlite(self, dialect, coltype):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return value
            return _json.loads(value) if isinstance(value, str) else value
        return process
    return _orig_jsonb_result(self, dialect, coltype)

PG_JSONB.bind_processor = _jsonb_bind_sqlite
PG_JSONB.result_processor = _jsonb_result_sqlite


# ── Set test environment BEFORE importing app code ────────────────────
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("POSTGRES_SERVER", "localhost")

from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.core.security import create_access_token
from app.core.limiter import limiter as shared_limiter

# ── Disable rate limiting for tests ──────────────────────────────────
shared_limiter.enabled = False

# ── Async SQLite engine (in-memory) ──────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

TestSessionLocal = sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Database lifecycle ───────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables once at start, drop at end."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    """Provide a clean database session per test (with rollback)."""
    async with TestSessionLocal() as session:
        yield session


# ── Override FastAPI dependency ──────────────────────────────────────

async def override_get_db():
    async with TestSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db


# ── HTTP client ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    """AsyncClient wired to the FastAPI test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Auth helpers ─────────────────────────────────────────────────────

def make_token(email: str = "test@example.com", org_id: str = None) -> str:
    """Create a valid JWT for testing."""
    return create_access_token(
        data={"sub": email, "org_id": org_id or str(uuid.uuid4())}
    )


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient):
    """Register a user and return (user_data, auth_headers, cookies)."""
    payload = {
        "full_name": "Test User",
        "org_name": "Test Org",
        "email": f"user_{uuid.uuid4().hex[:8]}@test.com",
        "password": "securepass123",
    }
    resp = await client.post(
        "/api/v1/auth/register",
        json=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200, f"Registration failed: {resp.text}"
    data = resp.json()

    # Extract the HttpOnly cookie set by the backend
    cookies = resp.cookies

    return {
        "user": data["user"],
        "token": data["access_token"],
        "email": payload["email"],
        "password": payload["password"],
        "headers": {
            "Authorization": f"Bearer {data['access_token']}",
            "X-Requested-With": "XMLHttpRequest",
        },
        "cookies": cookies,
    }
