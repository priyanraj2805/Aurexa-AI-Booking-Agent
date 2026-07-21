import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from app.models.organization import Organization
from app.models.user import User
from app.core.security import hash_password

from app.crud.monitoring import create_system_alert
from app.models.system_alert import SystemAlert
from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate


@pytest.mark.asyncio
async def test_monitoring_alerts_filters_unresolved_and_recent_hours(
    client, db_session, registered_user
):
    org_id = uuid.UUID(registered_user["user"]["org_id"])

    unresolved = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.EMAIL,
            severity=AlertSeverity.CRITICAL,
            code="SMTP_DOWN",
            message="smtp down",
        ),
    )
    resolved = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.WARNING,
            code="LLM_SLOW",
            message="llm slow",
        ),
    )
    stale = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.DATABASE,
            severity=AlertSeverity.CRITICAL,
            code="DB_TIMEOUT",
            message="db timeout",
        ),
    )

    await db_session.execute(
        update(SystemAlert)
        .where(SystemAlert.id == resolved.id)
        .values(resolved_at=datetime.now(timezone.utc))
    )
    await db_session.execute(
        update(SystemAlert)
        .where(SystemAlert.id == stale.id)
        .values(last_seen_at=datetime.now(timezone.utc) - timedelta(hours=6))
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/monitoring/alerts?unresolved_only=true&recent_hours=2&severity=critical",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    codes = [row["code"] for row in resp.json()]
    assert "SMTP_DOWN" in codes
    assert "LLM_SLOW" not in codes
    assert "DB_TIMEOUT" not in codes


@pytest.mark.asyncio
async def test_monitoring_alerts_maintains_org_isolation(
    client, db_session, registered_user
):
    my_org_id = uuid.UUID(registered_user["user"]["org_id"])
    other_org_id = uuid.uuid4()

    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=my_org_id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.CRITICAL,
            code="MINE_ONLY",
            message="mine",
        ),
    )
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=other_org_id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.CRITICAL,
            code="OTHER_ONLY",
            message="other",
        ),
    )
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            component=AlertComponent.DATABASE,
            severity=AlertSeverity.WARNING,
            code="GLOBAL_VISIBLE",
            message="global",
        ),
    )

    resp = await client.get(
        "/api/v1/monitoring/alerts", headers=registered_user["headers"]
    )
    assert resp.status_code == 200
    codes = [row["code"] for row in resp.json()]
    assert "MINE_ONLY" in codes
    assert "GLOBAL_VISIBLE" in codes
    assert "OTHER_ONLY" not in codes


@pytest.mark.asyncio
async def test_monitoring_alerts_includes_correlation_and_component_contract(
    client, db_session, registered_user
):
    org_id = uuid.UUID(registered_user["user"]["org_id"])

    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.EMAIL,
            severity=AlertSeverity.CRITICAL,
            code="EMAIL_PROVIDER_DOWN",
            message="email failed",
            request_id="req-email-1",
            trace_id="trace-email-1",
        ),
    )
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.CRITICAL,
            code="LLM_PROVIDER_DOWN",
            message="llm failed",
            request_id="req-llm-1",
            trace_id="trace-llm-1",
        ),
    )
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.DATABASE,
            severity=AlertSeverity.CRITICAL,
            code="DATABASE_ERROR",
            message="db failed",
            request_id="req-db-1",
            trace_id="trace-db-1",
        ),
    )

    resp = await client.get(
        "/api/v1/monitoring/alerts", headers=registered_user["headers"]
    )
    assert resp.status_code == 200
    rows = resp.json()
    by_code = {row["code"]: row for row in rows}
    assert by_code["EMAIL_PROVIDER_DOWN"]["component"] == "email"
    assert by_code["LLM_PROVIDER_DOWN"]["component"] == "llm"
    assert by_code["DATABASE_ERROR"]["component"] == "database"

    for code in ("EMAIL_PROVIDER_DOWN", "LLM_PROVIDER_DOWN", "DATABASE_ERROR"):
        row = by_code[code]
        assert row["request_id"] is not None
        assert row["trace_id"] is not None
        assert row["dedupe_key"] is not None
        assert row["occurrence_count"] >= 1


@pytest.mark.asyncio
async def test_monitoring_alerts_comm_03_component_contract(
    client, db_session, registered_user
):
    org_id = uuid.UUID(registered_user["user"]["org_id"])

    assert AlertComponent.EMAIL.value == "email"
    assert AlertComponent.LLM.value == "llm"
    assert AlertComponent.DATABASE.value == "database"

    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.TIMEZONE,
            severity=AlertSeverity.WARNING,
            code="TIMEZONE_FALLBACK_MISSING_TZ",
            message="timezone fallback used",
        ),
    )
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.BOOKING,
            severity=AlertSeverity.WARNING,
            code="ONLINE_ONLY_MEETING_LINK_UNAVAILABLE",
            message="meeting link unavailable",
            context={
                "provider": "ZOOM",
                "meeting_link_status": "UNAVAILABLE",
                "error_code": "TIMEOUT",
            },
        ),
    )

    resp = await client.get(
        "/api/v1/monitoring/alerts", headers=registered_user["headers"]
    )
    assert resp.status_code == 200
    by_code = {row["code"]: row for row in resp.json()}

    assert by_code["TIMEZONE_FALLBACK_MISSING_TZ"]["component"] == "timezone"
    assert by_code["ONLINE_ONLY_MEETING_LINK_UNAVAILABLE"]["component"] == "booking"


@pytest.mark.asyncio
async def test_monitoring_alerts_comm_03_dedupe_refresh_and_pii_safety(db_session):
    org_id = uuid.uuid4()
    payload = SystemAlertCreate(
        org_id=org_id,
        component=AlertComponent.TIMEZONE,
        severity=AlertSeverity.WARNING,
        code="TIMEZONE_FALLBACK_INVALID_TZ",
        message="timezone fallback used",
        context={"reason": "invalid_tz", "timezone_source": "fallback_org"},
    )

    first = await create_system_alert(db_session, payload)
    second = await create_system_alert(db_session, payload)

    assert first.id == second.id
    assert second.occurrence_count == 2
    assert second.dedupe_key == f"{org_id}:timezone:TIMEZONE_FALLBACK_INVALID_TZ"
    assert (second.context or {}).get("reason") == "invalid_tz"
    assert "email" not in (second.context or {})
    assert "phone" not in (second.context or {})
    assert "name" not in (second.context or {})


@pytest.mark.asyncio
async def test_monitoring_alerts_comm_03_org_isolation(
    client, db_session, registered_user
):
    my_org_id = uuid.UUID(registered_user["user"]["org_id"])
    other_org_id = uuid.uuid4()

    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=my_org_id,
            component=AlertComponent.BOOKING,
            severity=AlertSeverity.WARNING,
            code="ONLINE_ONLY_MEETING_LINK_UNAVAILABLE",
            message="mine",
            context={
                "provider": "ZOOM",
                "meeting_link_status": "UNAVAILABLE",
                "error_code": "TIMEOUT",
            },
        ),
    )
    await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=other_org_id,
            component=AlertComponent.BOOKING,
            severity=AlertSeverity.WARNING,
            code="ONLINE_ONLY_MEETING_LINK_UNAVAILABLE",
            message="other",
            context={
                "provider": "TEAMS",
                "meeting_link_status": "UNAVAILABLE",
                "error_code": "PROVIDER_5XX",
            },
        ),
    )

    resp = await client.get(
        "/api/v1/monitoring/alerts?component=booking",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert any(row["code"] == "ONLINE_ONLY_MEETING_LINK_UNAVAILABLE" for row in rows)
    mine = [
        row
        for row in rows
        if row["code"] == "ONLINE_ONLY_MEETING_LINK_UNAVAILABLE"
        and row["org_id"] == str(my_org_id)
    ]
    assert len(mine) == 1
    ctx = mine[0]["context"] or {}
    assert ctx.get("provider") == "ZOOM"
    assert ctx.get("meeting_link_status") == "UNAVAILABLE"
    assert ctx.get("error_code") == "TIMEOUT"
    assert "email" not in ctx
    assert "phone" not in ctx
    assert "name" not in ctx


@pytest.mark.asyncio
async def test_monitoring_escalation_policy_roundtrip(client, registered_user):
    payload = {
        "routing_targets": [
            {"target_type": "email", "target_value": "oncall@example.com"},
            {"target_type": "slack", "target_value": "#pager"},
        ],
        "rotation_windows": [
            {"day_of_week": 1, "start_hour_utc": 9, "end_hour_utc": 17},
            {"day_of_week": 5, "start_hour_utc": 10, "end_hour_utc": 14},
        ],
    }
    put_resp = await client.put(
        "/api/v1/monitoring/escalation-policy",
        json=payload,
        headers=registered_user["headers"],
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["org_id"] == registered_user["user"]["org_id"]
    assert len(body["routing_targets"]) == 2
    assert len(body["rotation_windows"]) == 2

    get_resp = await client.get(
        "/api/v1/monitoring/escalation-policy",
        headers=registered_user["headers"],
    )
    assert get_resp.status_code == 200
    fetched = get_resp.json()
    assert fetched["org_id"] == registered_user["user"]["org_id"]
    assert [t["target_type"] for t in fetched["routing_targets"]] == ["email", "slack"]


@pytest.mark.asyncio
async def test_monitoring_acknowledge_sets_ack_fields_not_resolved(
    client, db_session, registered_user
):
    org_id = uuid.UUID(registered_user["user"]["org_id"])
    alert = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.EMAIL,
            severity=AlertSeverity.CRITICAL,
            code="ACK_ME",
            message="acknowledge this alert",
        ),
    )
    resp = await client.post(
        f"/api/v1/monitoring/alerts/{alert.id}/acknowledge",
        json={"note": "Investigating issue"},
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["acknowledged_at"] is not None
    assert body["acknowledgement_note"] == "Investigating issue"
    assert body["acknowledged_by_user_id"] is not None
    assert body["resolved_at"] is None


@pytest.mark.asyncio
async def test_monitoring_acknowledge_is_org_scoped(
    client, db_session, registered_user
):
    other_org = Organization(
        name="Other Org",
        api_key=f"ds_other_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db_session.add(other_org)
    await db_session.flush()
    other_user = User(
        email=f"other_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("securepass123"),
        full_name="Other User",
        org_id=other_org.id,
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.flush()
    alert = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=other_org.id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.CRITICAL,
            code="OTHER_ORG_ALERT",
            message="other org only",
        ),
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/monitoring/alerts/{alert.id}/acknowledge",
        json={"note": "cross org attempt"},
        headers=registered_user["headers"],
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_monitoring_alert_trends_rejects_invalid_query_bounds(
    client, registered_user
):
    resp_window = await client.get(
        "/api/v1/monitoring/alerts/trends?window_hours=0",
        headers=registered_user["headers"],
    )
    assert resp_window.status_code == 422

    resp_bucket = await client.get(
        "/api/v1/monitoring/alerts/trends?bucket_minutes=4",
        headers=registered_user["headers"],
    )
    assert resp_bucket.status_code == 422


@pytest.mark.asyncio
async def test_monitoring_alert_trends_are_org_scoped(
    client, db_session, registered_user
):
    my_org_id = uuid.UUID(registered_user["user"]["org_id"])
    other_org_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    await db_session.execute(
        update(SystemAlert).values(last_seen_at=now - timedelta(days=30))
    )

    mine = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=my_org_id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.WARNING,
            code="TREND_MINE",
            message="mine",
        ),
    )
    other = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=other_org_id,
            component=AlertComponent.LLM,
            severity=AlertSeverity.CRITICAL,
            code="TREND_OTHER",
            message="other",
        ),
    )
    await db_session.execute(
        update(SystemAlert).where(SystemAlert.id == mine.id).values(last_seen_at=now)
    )
    await db_session.execute(
        update(SystemAlert).where(SystemAlert.id == other.id).values(last_seen_at=now)
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/monitoring/alerts/trends?window_hours=24&bucket_minutes=60&component=llm&severity=critical",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    # If org isolation is broken, this request will include OTHER_ORG critical LLM rows.
    assert all(bucket["total_alerts"] == 0 for bucket in body["series"])


@pytest.mark.asyncio
async def test_monitoring_alert_trends_anomaly_signal_spike_vs_stable(
    client, db_session, registered_user
):
    org_id = uuid.UUID(registered_user["user"]["org_id"])
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    await db_session.execute(
        update(SystemAlert).values(last_seen_at=now - timedelta(days=30))
    )

    baseline_offsets = [4, 3, 2]
    for idx, hour_offset in enumerate(baseline_offsets):
        alert = await create_system_alert(
            db_session,
            SystemAlertCreate(
                org_id=org_id,
                component=AlertComponent.EMAIL,
                severity=AlertSeverity.WARNING,
                code=f"TREND_BASE_{idx}",
                message="baseline",
            ),
        )
        await db_session.execute(
            update(SystemAlert)
            .where(SystemAlert.id == alert.id)
            .values(last_seen_at=now - timedelta(hours=hour_offset))
        )

    for idx in range(8):
        alert = await create_system_alert(
            db_session,
            SystemAlertCreate(
                org_id=org_id,
                component=AlertComponent.EMAIL,
                severity=AlertSeverity.CRITICAL,
                code=f"TREND_SPIKE_{idx}",
                message="spike",
            ),
        )
        await db_session.execute(
            update(SystemAlert)
            .where(SystemAlert.id == alert.id)
            .values(last_seen_at=now)
        )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/monitoring/alerts/trends?window_hours=6&bucket_minutes=60",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["series"]) >= 2
    anomaly_flags = [bucket["anomaly"]["is_anomaly"] for bucket in body["series"]]
    assert any(anomaly_flags)
    assert body["summary"]["anomaly_buckets_count"] >= 1


# Index decision for Phase 12:
# Existing `org_id` plus component/severity/time indexes are sufficient for OBS-04 windows;
# no additional migration is required for this phase scope.
