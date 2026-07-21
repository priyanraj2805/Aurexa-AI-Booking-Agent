from datetime import datetime, timedelta, timezone
import uuid

import pytest
import smtplib
from unittest.mock import AsyncMock

from app.crud.email_delivery import (
    create_delivery_log,
    mark_delivery_failed,
    mark_delivery_sent,
    mark_delivery_bounced,
    list_due_retries,
)
from app.crud.monitoring import create_system_alert
from app.schemas.email_delivery import EmailDeliveryCreate, EmailChannel
from app.schemas.monitoring import SystemAlertCreate, AlertComponent, AlertSeverity
from app.core.email_delivery_service import _classify_email_failure, _is_retryable_failure
from app.core.email_delivery_service import send_email_with_failover
from app.core.mail import send_confirmation_emails


def _to_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_mark_delivery_failed_backoff_attempt_1(db_session):
    rec = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.OTP,
            recipient_email="a@example.com",
            subject="OTP",
        ),
    )
    updated = await mark_delivery_failed(
        db_session,
        delivery_id=rec.id,
        error_message="temporary error",
        attempt_count=1,
        max_attempts=3,
    )
    assert updated.status == "pending"
    assert updated.next_retry_at is not None
    delta = _to_aware(updated.next_retry_at) - datetime.now(timezone.utc)
    assert 0 <= delta.total_seconds() <= 120


@pytest.mark.asyncio
async def test_mark_delivery_failed_backoff_attempt_2(db_session):
    rec = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.OTP,
            recipient_email="b@example.com",
            subject="OTP",
        ),
    )
    updated = await mark_delivery_failed(
        db_session,
        delivery_id=rec.id,
        error_message="temporary error",
        attempt_count=2,
        max_attempts=3,
    )
    assert updated.status == "pending"
    assert updated.next_retry_at is not None
    delta = _to_aware(updated.next_retry_at) - datetime.now(timezone.utc)
    assert 60 <= delta.total_seconds() <= 180


@pytest.mark.asyncio
async def test_mark_delivery_failed_terminal_attempt_3(db_session):
    rec = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.OTP,
            recipient_email="c@example.com",
            subject="OTP",
        ),
    )
    updated = await mark_delivery_failed(
        db_session,
        delivery_id=rec.id,
        error_message="permanent error",
        attempt_count=3,
        max_attempts=3,
    )
    assert updated.status == "failed"
    assert updated.next_retry_at is None


@pytest.mark.asyncio
async def test_mark_delivery_sent_clears_error_and_retry(db_session):
    rec = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.OTP,
            recipient_email="d@example.com",
            subject="OTP",
        ),
    )
    await mark_delivery_failed(
        db_session,
        delivery_id=rec.id,
        error_message="temporary",
        attempt_count=1,
        max_attempts=3,
    )
    sent = await mark_delivery_sent(
        db_session, delivery_id=rec.id, provider="secondary", attempt_count=2
    )
    assert sent.status == "sent"
    assert sent.provider == "secondary"
    assert sent.last_error is None
    assert sent.next_retry_at is None


@pytest.mark.asyncio
async def test_mark_delivery_bounced_terminal(db_session):
    rec = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.BOOKING_CONFIRMATION,
            recipient_email="bounce@example.com",
            subject="Booking",
        ),
    )
    bounced = await mark_delivery_bounced(
        db_session,
        delivery_id=rec.id,
        error_message="550 mailbox unavailable",
        provider="primary",
    )
    assert bounced.status == "bounced"
    assert bounced.next_retry_at is None


@pytest.mark.asyncio
async def test_list_due_retries_returns_due_only(db_session):
    now = datetime.now(timezone.utc)
    due = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.OTP,
            recipient_email="due@example.com",
            subject="OTP",
        ),
    )
    not_due = await create_delivery_log(
        db_session,
        EmailDeliveryCreate(
            org_id=uuid.uuid4(),
            channel=EmailChannel.OTP,
            recipient_email="later@example.com",
            subject="OTP",
        ),
    )
    await mark_delivery_failed(
        db_session, delivery_id=due.id, error_message="x", attempt_count=1, max_attempts=3
    )
    await mark_delivery_failed(
        db_session, delivery_id=not_due.id, error_message="x", attempt_count=1, max_attempts=3
    )
    # move "due" into past
    due.next_retry_at = now - timedelta(seconds=1)
    not_due.next_retry_at = now + timedelta(minutes=5)
    await db_session.commit()

    rows = await list_due_retries(db_session, as_of=now)
    ids = {r.id for r in rows}
    assert due.id in ids
    assert not_due.id not in ids


@pytest.mark.asyncio
async def test_create_system_alert_persists(db_session):
    alert = await create_system_alert(
        db_session,
        SystemAlertCreate(
            component=AlertComponent.EMAIL,
            severity=AlertSeverity.CRITICAL,
            code="EMAIL_PROVIDER_DOWN",
            message="both providers failed",
            request_id="req-alert-1",
            trace_id="trace-alert-1",
            context={"attempt": 3},
        ),
    )
    assert alert.code == "EMAIL_PROVIDER_DOWN"
    assert alert.component == "email"
    assert alert.severity == "critical"
    assert alert.request_id == "req-alert-1"
    assert alert.trace_id == "trace-alert-1"
    assert alert.occurrence_count == 1


@pytest.mark.asyncio
async def test_create_system_alert_dedupes_unresolved_rows(db_session):
    org_id = uuid.uuid4()
    dedupe_code = f"EMAIL_PROVIDER_DOWN_{uuid.uuid4().hex[:8]}"
    first = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.EMAIL,
            severity=AlertSeverity.CRITICAL,
            code=dedupe_code,
            message="first",
            request_id="req-alert-a",
        ),
    )
    second = await create_system_alert(
        db_session,
        SystemAlertCreate(
            org_id=org_id,
            component=AlertComponent.EMAIL,
            severity=AlertSeverity.CRITICAL,
            code=dedupe_code,
            message="second",
            request_id="req-alert-b",
        ),
    )
    assert first.id == second.id
    assert second.occurrence_count == 2
    assert second.request_id == "req-alert-b"


def test_email_failure_classification_timeout():
    assert _classify_email_failure(TimeoutError("timed out")) == "timeout"
    assert _is_retryable_failure("timeout") is True


def test_email_failure_classification_provider_down():
    assert _classify_email_failure(ConnectionError("connection refused")) == "provider_down"
    assert _is_retryable_failure("provider_down") is True


def test_email_failure_classification_auth_error():
    err = smtplib.SMTPAuthenticationError(535, b"auth failed")
    assert _classify_email_failure(err) == "auth_error"
    assert _is_retryable_failure("auth_error") is False


def test_email_failure_classification_invalid_config():
    err = RuntimeError("primary SMTP credentials not configured")
    assert _classify_email_failure(err) == "invalid_config"
    assert _is_retryable_failure("invalid_config") is False


def test_email_failure_classification_bounce():
    err = smtplib.SMTPRecipientsRefused({"x@test.com": (550, b"mailbox unavailable")})
    assert _classify_email_failure(err) == "bounce"
    assert _is_retryable_failure("bounce") is False


@pytest.mark.asyncio
async def test_terminal_email_failure_emits_alert_with_correlation(monkeypatch):
    org_id = uuid.uuid4()
    emit_mock = AsyncMock()
    monkeypatch.setattr("app.core.email_delivery_service._emit_email_alert", emit_mock)
    dummy_db = object()

    class DummySessionCtx:
        async def __aenter__(self):
            return dummy_db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.core.email_delivery_service.AsyncSessionLocal", lambda: DummySessionCtx())

    async def _always_fail(*_args, **_kwargs):
        return False, RuntimeError("primary SMTP credentials not configured")

    monkeypatch.setattr("app.core.email_delivery_service._attempt_provider_send", _always_fail)
    monkeypatch.setattr("app.core.email_delivery_service.secondary_provider", lambda: None)
    monkeypatch.setattr(
        "app.core.email_delivery_service.create_delivery_log",
        AsyncMock(return_value=type("Delivery", (), {"id": uuid.uuid4()})()),
    )
    monkeypatch.setattr("app.core.email_delivery_service.mark_delivery_failed", AsyncMock())

    with pytest.raises(RuntimeError):
        await send_email_with_failover(
            org_id=org_id,
            channel="otp",
            to_email="corr@test.com",
            subject="OTP",
            body_html="<p>x</p>",
            request_id="req-corr-1",
            trace_id="trace-corr-1",
            payload={"subject": "OTP", "body_html": "<p>x</p>"},
        )

    emit_mock.assert_awaited_once()
    assert emit_mock.await_args.kwargs["request_id"] == "req-corr-1"
    assert emit_mock.await_args.kwargs["trace_id"] == "trace-corr-1"


@pytest.mark.asyncio
async def test_send_email_with_failover_dedupes_idempotency_key(monkeypatch):
    org_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    send_mock = AsyncMock(return_value=(True, None))
    class _Rows:
        def __init__(self, rows):
            self._rows = rows
        def scalars(self):
            return self
        def all(self):
            return self._rows

    class _DummyDB:
        def __init__(self):
            self.calls = 0
        async def execute(self, _stmt):
            self.calls += 1
            if self.calls == 1:
                return _Rows([])
            row = type("DeliveryRow", (), {"id": delivery_id, "payload": {"idempotency_key": "otp:s1:confirm"}, "status": "sent"})()
            return _Rows([row])
        async def commit(self):
            return None
    dummy_db = _DummyDB()

    class DummySessionCtx:
        async def __aenter__(self):
            return dummy_db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.core.email_delivery_service.AsyncSessionLocal", lambda: DummySessionCtx())
    monkeypatch.setattr(
        "app.core.email_delivery_service.create_delivery_log",
        AsyncMock(return_value=type("Delivery", (), {"id": delivery_id})()),
    )
    monkeypatch.setattr("app.core.email_delivery_service.get_delivery_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr("app.core.email_delivery_service.mark_delivery_sent", AsyncMock())
    monkeypatch.setattr("app.core.email_delivery_service.mark_delivery_failed", AsyncMock())
    monkeypatch.setattr("app.core.email_delivery_service._attempt_provider_send", send_mock)

    payload = {"subject": "OTP", "body_html": "<p>OTP</p>", "idempotency_key": "otp:s1:confirm"}
    first = await send_email_with_failover(
        org_id=org_id,
        channel="otp",
        to_email="user@example.com",
        subject="OTP",
        body_html="<p>OTP</p>",
        payload=payload,
    )
    second = await send_email_with_failover(
        org_id=org_id,
        channel="otp",
        to_email="user@example.com",
        subject="OTP",
        body_html="<p>OTP</p>",
        payload=payload,
    )

    assert first == second
    assert send_mock.await_count == 1


@pytest.mark.asyncio
async def test_send_confirmation_emails_dedupes_by_booking_and_role(monkeypatch):
    send_mock = AsyncMock()
    monkeypatch.setattr("app.core.mail.send_email_with_failover", send_mock)

    booking_details = {
        "booking_id": "booking-123",
        "name": "Deduped User",
        "email": "dedupe@example.com",
        "date": "2030-01-10",
        "time": "10:00",
    }
    org_id = uuid.uuid4()

    await send_confirmation_emails(
        "dedupe@example.com",
        "admin@example.com",
        booking_details,
        org_id=org_id,
    )
    await send_confirmation_emails(
        "dedupe@example.com",
        "admin@example.com",
        booking_details,
        org_id=org_id,
    )

    assert send_mock.await_count == 2
    channels = [call.kwargs["channel"] for call in send_mock.await_args_list]
    assert channels == ["booking_confirmation", "booking_confirmation"]

