import functools
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from uuid import UUID

import anyio
import logging
from sqlalchemy.future import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.crud.email_delivery import (
    create_delivery_log,
    get_delivery_by_id,
    list_due_retries,
    mark_delivery_bounced,
    mark_delivery_failed,
    mark_delivery_sent,
)
from app.models.email_delivery import EmailDeliveryLog
from app.crud.monitoring import create_system_alert
from app.schemas.email_delivery import EmailChannel, EmailDeliveryCreate
from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SMTPProvider:
    name: str
    host: str
    port: int
    username: str
    password: str
    from_email: str


def primary_provider() -> SMTPProvider:
    return SMTPProvider(
        name="primary",
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        from_email=settings.EMAILS_FROM_EMAIL,
    )


def secondary_provider() -> Optional[SMTPProvider]:
    if not settings.SMTP_SECONDARY_HOST:
        return None
    return SMTPProvider(
        name="secondary",
        host=settings.SMTP_SECONDARY_HOST,
        port=settings.SMTP_SECONDARY_PORT,
        username=settings.SMTP_SECONDARY_USER,
        password=settings.SMTP_SECONDARY_PASSWORD,
        from_email=settings.SMTP_SECONDARY_FROM_EMAIL or settings.SMTP_SECONDARY_USER,
    )


def check_email_service_health() -> dict:
    primary = primary_provider()
    secondary = secondary_provider()
    return {
        "primary": {
            "status": "ok" if (primary.username and primary.password) else "down"
        },
        "secondary": {
            "status": (
                "not_configured"
                if secondary is None
                else ("ok" if secondary.username and secondary.password else "down")
            )
        },
    }


def _is_bounce_error(err: Exception) -> bool:
    msg = str(err).lower()
    return isinstance(err, smtplib.SMTPRecipientsRefused) or "550" in msg or "mailbox unavailable" in msg


def _classify_email_failure(err: Exception) -> str:
    msg = str(err).lower()
    if _is_bounce_error(err):
        return "bounce"
    if isinstance(err, (TimeoutError, smtplib.SMTPServerDisconnected)) or "timeout" in msg:
        return "timeout"
    if (
        isinstance(err, smtplib.SMTPAuthenticationError)
        or "authentication" in msg
        or "auth" in msg
    ):
        return "auth_error"
    if "not configured" in msg or "credentials" in msg:
        return "invalid_config"
    if (
        isinstance(err, (ConnectionError, OSError, smtplib.SMTPConnectError))
        or "connection refused" in msg
        or "unreachable" in msg
    ):
        return "provider_down"
    return "provider_down"


def _is_retryable_failure(failure_class: str) -> bool:
    return failure_class in {"timeout", "provider_down"}


def _send_via_provider(
    provider: SMTPProvider,
    *,
    to_email: str,
    subject: str,
    body_html: str,
) -> None:
    if not provider.username or not provider.password:
        raise RuntimeError(f"{provider.name} SMTP credentials not configured")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = provider.from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(provider.host, provider.port, timeout=10) as server:
        server.starttls()
        server.login(provider.username, provider.password)
        server.send_message(msg)


async def _emit_email_alert(
    *,
    org_id: Optional[UUID],
    code: str,
    message: str,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    async with AsyncSessionLocal() as db:
        await create_system_alert(
            db,
            SystemAlertCreate(
                org_id=org_id,
                component=AlertComponent.EMAIL,
                severity=AlertSeverity.CRITICAL,
                code=code,
                message=message,
                request_id=request_id,
                trace_id=trace_id,
                context=context,
            ),
        )


async def _attempt_provider_send(
    *,
    provider: SMTPProvider,
    org_id: UUID,
    request_id: Optional[str],
    delivery_id: UUID,
    attempt_count: int,
    to_email: str,
    subject: str,
    body_html: str,
) -> tuple[bool, Optional[Exception]]:
    try:
        fn = functools.partial(
            _send_via_provider,
            provider,
            to_email=to_email,
            subject=subject,
            body_html=body_html,
        )
        await anyio.to_thread.run_sync(fn)
        logger.info(
            "email.send.success",
            extra={
                "event": "email.send.success",
                "org_id": str(org_id),
                "request_id": request_id,
                "provider": provider.name,
                "attempt_count": attempt_count,
                "email_delivery_id": str(delivery_id),
            },
        )
        return True, None
    except Exception as err:
        logger.warning(
            "email.send.failed",
            extra={
                "event": "email.send.failed",
                "org_id": str(org_id),
                "request_id": request_id,
                "provider": provider.name,
                "attempt_count": attempt_count,
                "email_delivery_id": str(delivery_id),
                "error": str(err),
            },
        )
        return False, err


async def send_email_with_failover(
    *,
    org_id: UUID,
    channel: str,
    to_email: str,
    subject: str,
    body_html: str,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    delivery_id: Optional[UUID] = None,
    payload: Optional[dict] = None,
    attempt_count: int = 1,
) -> UUID:
    to_email = str(to_email or "").strip()
    if not to_email:
        raise ValueError("Email recipient is required")
    max_attempts = max(1, settings.EMAIL_RETRY_MAX_ATTEMPTS)
    idempotency_key = None
    if isinstance(payload, dict):
        raw_key = payload.get("idempotency_key")
        if raw_key:
            idempotency_key = str(raw_key)
    async with AsyncSessionLocal() as db:
        if idempotency_key and not delivery_id:
            existing_rows = await db.execute(
                select(EmailDeliveryLog)
                .where(
                    EmailDeliveryLog.org_id == org_id,
                    EmailDeliveryLog.channel == channel,
                    EmailDeliveryLog.recipient_email == to_email,
                )
                .order_by(EmailDeliveryLog.created_at.desc())
            )
            for row in existing_rows.scalars().all():
                row_payload = row.payload if isinstance(row.payload, dict) else {}
                if row_payload.get("idempotency_key") != idempotency_key:
                    continue
                if row.status == "sent":
                    return row.id
                if row.status == "pending":
                    delivery_id = row.id
                    break
        if delivery_id:
            existing = await get_delivery_by_id(db, delivery_id)
            if existing:
                existing.attempt_count = attempt_count
                if payload:
                    existing.payload = payload
                await db.commit()
        if not delivery_id:
            created = await create_delivery_log(
                db,
                EmailDeliveryCreate(
                    org_id=org_id,
                    channel=EmailChannel(channel),
                    recipient_email=to_email,
                    subject=subject,
                    provider="primary",
                    request_id=request_id,
                    max_attempts=max_attempts,
                    payload=payload,
                ),
            )
            delivery_id = created.id

    primary = primary_provider()
    secondary = secondary_provider()

    sent, err = await _attempt_provider_send(
        provider=primary,
        org_id=org_id,
        request_id=request_id,
        delivery_id=delivery_id,
        attempt_count=attempt_count,
        to_email=to_email,
        subject=subject,
        body_html=body_html,
    )
    if sent:
        async with AsyncSessionLocal() as db:
            await mark_delivery_sent(
                db, delivery_id=delivery_id, provider="primary", attempt_count=attempt_count
            )
        return delivery_id

    logger.info(
        "email.send.failover",
        extra={
            "event": "email.send.failover",
            "org_id": str(org_id),
            "request_id": request_id,
            "provider": "primary",
            "attempt_count": attempt_count,
            "email_delivery_id": str(delivery_id),
        },
    )

    if secondary:
        sent2, err2 = await _attempt_provider_send(
            provider=secondary,
            org_id=org_id,
            request_id=request_id,
            delivery_id=delivery_id,
            attempt_count=attempt_count,
            to_email=to_email,
            subject=subject,
            body_html=body_html,
        )
        if sent2:
            async with AsyncSessionLocal() as db:
                await mark_delivery_sent(
                    db, delivery_id=delivery_id, provider="secondary", attempt_count=attempt_count
                )
            return delivery_id
        err = err2 or err

    failure_class = _classify_email_failure(err) if err else "provider_down"
    async with AsyncSessionLocal() as db:
        if err and _is_bounce_error(err):
            await mark_delivery_bounced(
                db,
                delivery_id=delivery_id,
                error_message=str(err),
                provider="secondary" if secondary else "primary",
            )
        elif not _is_retryable_failure(failure_class):
            await mark_delivery_failed(
                db,
                delivery_id=delivery_id,
                error_message=str(err) if err else "Unknown email delivery failure",
                attempt_count=max_attempts,
                max_attempts=max_attempts,
            )
        else:
            await mark_delivery_failed(
                db,
                delivery_id=delivery_id,
                error_message=str(err) if err else "Unknown email delivery failure",
                attempt_count=attempt_count,
                max_attempts=max_attempts,
            )

    if not _is_retryable_failure(failure_class) or attempt_count >= max_attempts:
        await _emit_email_alert(
            org_id=org_id,
            code="EMAIL_PROVIDER_DOWN",
            message="Both email providers failed to deliver message",
            request_id=request_id,
            trace_id=trace_id,
            context={
                "delivery_id": str(delivery_id),
                "attempt_count": attempt_count,
                "failure_class": failure_class,
            },
        )
    raise RuntimeError(f"Email delivery failed for {to_email}")


async def process_due_email_retries(limit: int = 100) -> int:
    processed = 0
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        due = await list_due_retries(db, as_of=now, limit=limit)

    for delivery in due:
        payload = delivery.payload or {}
        body_html = payload.get("body_html")
        subject = payload.get("subject")
        if not subject or not body_html:
            # Can't reconstruct payload safely; mark terminal failed
            async with AsyncSessionLocal() as db:
                await mark_delivery_failed(
                    db,
                    delivery_id=delivery.id,
                    error_message="Retry payload missing subject/body_html",
                    attempt_count=delivery.max_attempts,
                    max_attempts=delivery.max_attempts,
                )
            continue
        try:
            await send_email_with_failover(
                org_id=delivery.org_id,
                channel=delivery.channel,
                to_email=delivery.recipient_email,
                subject=subject,
                body_html=body_html,
                request_id=delivery.request_id,
                delivery_id=delivery.id,
                payload=payload,
                attempt_count=(delivery.attempt_count or 0) + 1,
            )
        except Exception:
            # status already updated in send_email_with_failover
            pass
        processed += 1
    return processed

