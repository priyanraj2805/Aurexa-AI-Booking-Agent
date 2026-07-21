from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.core.tasks import archive_inactive_sessions, run_scheduled_archival


async def _make_org_with_config(
    db_session,
    *,
    retention_days: int = 30,
    cleanup_frequency: str = "daily",
    cleanup_hour_utc: int = 0,
):
    org = Organization(
        name=f"Archival Org {uuid.uuid4().hex[:6]}",
        api_key=f"ds_arch_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()
    cfg = OrgConfig(
        org_id=org.id,
        required_fields=["name", "email", "phone", "date", "time"],
        session_retention_days=retention_days,
        session_cleanup_frequency=cleanup_frequency,
        session_cleanup_hour_utc=cleanup_hour_utc,
    )
    db_session.add(cfg)
    await db_session.commit()
    return org, cfg


@pytest.mark.asyncio
async def test_archive_inactive_sessions_archives_older_than_retention(db_session):
    now = datetime.now(timezone.utc)
    org, _ = await _make_org_with_config(db_session, retention_days=30)
    stale = ChatSession(
        org_id=org.id,
        state_data={},
        missing_fields=[],
        session_status="ACTIVE",
        is_active=True,
        updated_at=now - timedelta(days=45),
    )
    fresh = ChatSession(
        org_id=org.id,
        state_data={},
        missing_fields=[],
        session_status="ACTIVE",
        is_active=True,
        updated_at=now - timedelta(days=5),
    )
    db_session.add_all([stale, fresh])
    await db_session.commit()

    archived = await archive_inactive_sessions(db_session, now=now)
    assert archived == 1

    await db_session.refresh(stale)
    await db_session.refresh(fresh)
    assert stale.session_status == "ARCHIVED"
    assert stale.archived_at is not None
    assert stale.is_active is False
    assert fresh.session_status == "ACTIVE"
    assert fresh.archived_at is None


@pytest.mark.asyncio
async def test_archive_inactive_sessions_is_idempotent(db_session):
    now = datetime.now(timezone.utc)
    org, _ = await _make_org_with_config(db_session, retention_days=30)
    stale = ChatSession(
        org_id=org.id,
        state_data={},
        missing_fields=[],
        session_status="ACTIVE",
        is_active=True,
        updated_at=now - timedelta(days=60),
    )
    db_session.add(stale)
    await db_session.commit()

    first = await archive_inactive_sessions(db_session, now=now)
    second = await archive_inactive_sessions(db_session, now=now + timedelta(minutes=5))
    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_run_scheduled_archival_daily_due_and_not_due(db_session):
    now = datetime(2026, 3, 29, 10, 0, tzinfo=timezone.utc)
    org, _ = await _make_org_with_config(
        db_session, retention_days=30, cleanup_frequency="daily", cleanup_hour_utc=9
    )
    stale = ChatSession(
        org_id=org.id,
        state_data={},
        missing_fields=[],
        session_status="ACTIVE",
        is_active=True,
        updated_at=now - timedelta(days=90),
    )
    db_session.add(stale)
    await db_session.commit()

    run_state = {}
    first = await run_scheduled_archival(db_session, now=now, last_runs=run_state)
    second = await run_scheduled_archival(
        db_session, now=now + timedelta(minutes=5), last_runs=run_state
    )
    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_run_scheduled_archival_deterministic_after_restart(db_session):
    now = datetime(2026, 3, 29, 10, 0, tzinfo=timezone.utc)
    org, _ = await _make_org_with_config(
        db_session, retention_days=30, cleanup_frequency="daily", cleanup_hour_utc=9
    )
    stale = ChatSession(
        org_id=org.id,
        state_data={},
        missing_fields=[],
        session_status="ACTIVE",
        is_active=True,
        updated_at=now - timedelta(days=90),
    )
    db_session.add(stale)
    await db_session.commit()

    # Fresh run state simulates process restart; idempotent update should still avoid re-mutation.
    first = await run_scheduled_archival(db_session, now=now, last_runs={})
    second = await run_scheduled_archival(
        db_session, now=now + timedelta(minutes=5), last_runs={}
    )
    assert first == 1
    assert second == 0
