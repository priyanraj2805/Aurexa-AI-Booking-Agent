import pytest
from unittest.mock import patch

from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate
from app.crud.monitoring import create_system_alert


@pytest.mark.asyncio
async def test_health_response_has_dependency_checks(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "request_id" in body
    assert "trace_id" in body
    assert "checks" in body
    assert "database" in body["checks"]
    assert "llm" in body["checks"]
    assert "email" in body["checks"]


@pytest.mark.asyncio
async def test_health_is_poll_safe_when_dependencies_are_down(client):
    with patch(
        "app.core.health_status.check_email_service_health",
        return_value={"primary": {"status": "down"}, "secondary": {"status": "down"}},
    ), patch("app.core.errors.create_system_alert") as alert_mock:
        for _ in range(3):
            resp = await client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["checks"]["email"]["status"] == "down"
            assert "Primary and fallback providers unavailable" in (
                body["checks"]["email"]["error"] or ""
            )
    alert_mock.assert_not_called()


@pytest.mark.asyncio
async def test_monitoring_alerts_requires_auth(client):
    resp = await client.get("/api/v1/monitoring/alerts")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_monitoring_alerts_returns_rows_for_org(client, db_session, registered_user):
    # create global and org alert; endpoint should include org-scoped/global rows
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            component=AlertComponent.LLM,
            severity=AlertSeverity.CRITICAL,
            code="LLM_UNAVAILABLE",
            message="llm down",
            request_id="req-health-r1",
            trace_id="trace-health-r1",
            context={"request_id": "r1"},
        ),
    )

    resp = await client.get(
        "/api/v1/monitoring/alerts",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    row = next(row for row in data if row["code"] == "LLM_UNAVAILABLE")
    assert row["request_id"] == "req-health-r1"
    assert row["trace_id"] == "trace-health-r1"
    assert row["dedupe_key"] is not None
    assert row["occurrence_count"] >= 1

