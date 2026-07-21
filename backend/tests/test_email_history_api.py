import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.models.organization import Organization
from app.schemas.email_delivery import EmailDeliveryCreate, EmailChannel
from app.crud.email_delivery import create_delivery_log, mark_delivery_failed


@pytest.mark.asyncio
async def test_email_history_unauthenticated_returns_401(client):
    resp = await client.get("/api/v1/email/history")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_email_history_is_org_scoped(client, db_session: AsyncSession, registered_user):
    user_email = registered_user["email"]
    user = (await db_session.execute(select(User).where(User.email == user_email))).scalars().first()
    assert user is not None

    # own org record
    await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=user.org_id,
            channel=EmailChannel.OTP,
            recipient_email="mine@example.com",
            subject="OTP",
        ),
    )

    # foreign org record
    foreign_org = Organization(
        name="Foreign Org",
        api_key=f"ds_test_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db_session.add(foreign_org)
    await db_session.commit()
    await db_session.refresh(foreign_org)

    await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=foreign_org.id,
            channel=EmailChannel.OTP,
            recipient_email="foreign@example.com",
            subject="OTP",
        ),
    )

    resp = await client.get("/api/v1/email/history", headers=registered_user["headers"])
    assert resp.status_code == 200
    data = resp.json()
    recipients = {row["recipient_email"] for row in data}
    assert "mine@example.com" in recipients
    assert "foreign@example.com" not in recipients


@pytest.mark.asyncio
async def test_email_history_filters_by_status(client, db_session: AsyncSession, registered_user):
    user = (await db_session.execute(select(User).where(User.email == registered_user["email"]))).scalars().first()
    assert user is not None

    sent = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=user.org_id,
            channel=EmailChannel.OTP,
            recipient_email="sent@example.com",
            subject="OTP",
        ),
    )
    failed = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=user.org_id,
            channel=EmailChannel.OTP,
            recipient_email="failed@example.com",
            subject="OTP",
        ),
    )
    await mark_delivery_failed(
        db_session,
        delivery_id=failed.id,
        error_message="smtp down",
        attempt_count=3,
        max_attempts=3,
    )
    sent.status = "sent"
    await db_session.commit()

    resp = await client.get(
        "/api/v1/email/history?status=failed",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(row["status"] == "failed" for row in data)
    assert any(row["recipient_email"] == "failed@example.com" for row in data)


@pytest.mark.asyncio
async def test_email_history_filters_by_date_range(client, db_session: AsyncSession, registered_user):
    user = (await db_session.execute(select(User).where(User.email == registered_user["email"]))).scalars().first()
    assert user is not None

    older = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=user.org_id,
            channel=EmailChannel.OTP,
            recipient_email="older@example.com",
            subject="OTP",
        ),
    )
    recent = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=user.org_id,
            channel=EmailChannel.OTP,
            recipient_email="recent@example.com",
            subject="OTP",
        ),
    )

    older.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    recent.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    from_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    resp = await client.get(
        f"/api/v1/email/history?from_date={from_date}",
        headers=registered_user["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    recipients = {row["recipient_email"] for row in data}
    assert "recent@example.com" in recipients
    assert "older@example.com" not in recipients

