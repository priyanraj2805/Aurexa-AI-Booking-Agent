import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from unittest.mock import AsyncMock
from fastapi import FastAPI

from app.core.errors import add_exception_handlers


@pytest.mark.asyncio
async def test_sqlalchemy_handler_emits_database_alert_and_preserves_response_shape(monkeypatch):
    test_app = FastAPI()
    add_exception_handlers(test_app)

    @test_app.get("/_test/sqlalchemy-error")
    async def _raise_sqlalchemy_error():
        raise SQLAlchemyError("simulated db failure")

    dummy_db = object()

    class DummySessionCtx:
        async def __aenter__(self):
            return dummy_db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    create_alert_mock = AsyncMock()
    monkeypatch.setattr("app.core.errors.AsyncSessionLocal", lambda: DummySessionCtx())
    monkeypatch.setattr("app.core.errors.create_system_alert", create_alert_mock)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/_test/sqlalchemy-error",
            headers={"x-request-id": "req-db-e1", "x-trace-id": "trace-db-e1"},
        )

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "DATABASE_ERROR"
    assert "internal database error" in body["error"]["message"].lower()

    create_alert_mock.assert_awaited_once()
    alert_payload = create_alert_mock.await_args.args[1]
    assert alert_payload.component.value == "database"
    assert alert_payload.severity.value == "critical"
    assert alert_payload.code == "DATABASE_ERROR"
    assert alert_payload.request_id == "req-db-e1"
    assert alert_payload.trace_id == "trace-db-e1"
