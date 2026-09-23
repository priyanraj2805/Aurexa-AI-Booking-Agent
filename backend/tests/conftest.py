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
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB as PG_JSONB, TSVECTOR as PG_TSVECTOR

@compiles(PG_UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


@compiles(PG_JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PG_TSVECTOR, "sqlite")
def compile_tsvector_sqlite(type_, compiler, **kw):
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
os.environ.setdefault("TELEMETRY_ENABLED", "false")
# Dummy LLM creds so ChatOpenAI can init without real provider; actual calls are mocked/fail gracefully
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.setdefault("LLM_BASE_URL", "https://example.com/v1")

from app.db.base import Base
from app.db.database import get_db
from app.main import app as fastapi_app
from app.core.security import create_access_token
from app.core.limiter import limiter as shared_limiter

# Ensure all models are imported before Base.metadata.create_all so
# tables like org_notifications / document_chunks are created in sqlite.
# Use importlib to avoid binding `app` name locally (would shadow fastapi_app).
import importlib as _importlib

for _mod in [
    "app.models.organization",
    "app.models.user",
    "app.models.config",
    "app.models.booking",
    "app.models.session",
    "app.models.message",
    "app.models.otp",
    "app.models.whatsapp_connection",
    "app.models.system_alert",
    "app.models.audit_log",
    "app.models.org_notification",
    "app.models.email_delivery",
    "app.models.document_chunk",
    "app.models.service_unit",
    "app.models.rbac",
    "app.models.razorpay_route",
    "app.models.payment",
    "app.models.booking_directive",
    "app.models.escalation_policy",
    "app.models.login_code",
]:
    _importlib.import_module(_mod)

# ── Disable rate limiting for tests ──────────────────────────────────
shared_limiter.enabled = False

# Keep `app` alias for backward-compat with fixtures that expect `app`
app = fastapi_app



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

# ── Patch background sessions to use test engine ────────────────
# app.core.org_notify / email_delivery_service / telemetry create their
# own AsyncSessionLocal bound to real Postgres. For tests we rebind them
# to the sqlite test engine so best-effort inserts hit the test DB.
try:
    import app.core.org_notify as _org_notify_mod

    _org_notify_mod.AsyncSessionLocal = TestSessionLocal
except Exception:
    pass
try:
    import app.core.email_delivery_service as _email_svc_mod

    _email_svc_mod.AsyncSessionLocal = TestSessionLocal
except Exception:
    pass
try:
    import app.core.telemetry as _tele_mod

    if hasattr(_tele_mod, "AsyncSessionLocal"):
        _tele_mod.AsyncSessionLocal = TestSessionLocal
except Exception:
    pass
try:
    import app.core.tasks as _tasks_mod

    _tasks_mod.AsyncSessionLocal = TestSessionLocal
except Exception:
    pass


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
async def auth_helpers(client: AsyncClient, monkeypatch):
    """Patches OTP email sending and provides helpers for the OTP-gated flows.

    Registration requires a 6-digit email OTP; login is a plain email +
    password flow. The real mail sender is replaced with an in-memory
    catcher so tests can complete the flows with the codes the backend
    "emailed".
    """
    sent: list[dict] = []

    async def _fake_send(to_email: str, code: str, purpose: str):
        sent.append({"to_email": to_email, "code": code, "purpose": purpose})

    monkeypatch.setattr("app.api.v1.endpoints.auth.send_auth_otp_email", _fake_send)

    def latest_code(email: str, purpose: str) -> str:
        for item in reversed(sent):
            if item["to_email"] == email and item["purpose"] == purpose:
                return item["code"]
        raise AssertionError(f"No OTP captured for {email}/{purpose}")

    async def register(payload: dict):
        resp = await client.post(
            "/api/v1/auth/otp/send",
            json={"email": payload["email"], "purpose": "register"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200, f"OTP send failed: {resp.text}"
        code = latest_code(payload["email"], "register")
        return await client.post(
            "/api/v1/auth/register",
            json={**payload, "otp_code": code},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    async def login(email: str, password: str, headers: dict | None = None):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=headers or {"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        assert "access_token" in resp.json(), "Login must issue a session directly"
        return resp

    return {
        "sent": sent,
        "latest_code": latest_code,
        "register": register,
        "login": login,
    }


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient, auth_helpers):
    """Register a user (OTP-gated) and return (user_data, auth_headers, cookies)."""
    payload = {
        "full_name": "Test User",
        "org_name": "Test Org",
        "email": f"user_{uuid.uuid4().hex[:8]}@test.com",
        "password": "securepass123",
    }
    resp = await auth_helpers["register"](payload)
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
