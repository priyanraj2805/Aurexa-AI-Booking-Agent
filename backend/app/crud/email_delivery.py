from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.email_delivery import EmailDeliveryLog
from app.schemas.email_delivery import EmailDeliveryCreate, EmailHistoryFilter


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def create_delivery_log(db: AsyncSession, payload: EmailDeliveryCreate) -> EmailDeliveryLog:
    record = EmailDeliveryLog(
        org_id=payload.org_id,
        channel=payload.channel.value if hasattr(payload.channel, "value") else payload.channel,
        recipient_email=payload.recipient_email,
        subject=payload.subject,
        provider=payload.provider,
        status="pending",
        attempt_count=0,
        max_attempts=payload.max_attempts,
        request_id=payload.request_id,
        payload=payload.payload,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_delivery_by_id(db: AsyncSession, delivery_id: UUID) -> Optional[EmailDeliveryLog]:
    result = await db.execute(select(EmailDeliveryLog).where(EmailDeliveryLog.id == delivery_id))
    return result.scalars().first()


async def mark_delivery_sent(
    db: AsyncSession,
    *,
    delivery_id: UUID,
    provider: str,
    attempt_count: int,
) -> Optional[EmailDeliveryLog]:
    record = await get_delivery_by_id(db, delivery_id)
    if not record:
        return None
    record.status = "sent"
    record.provider = provider
    record.attempt_count = attempt_count
    record.next_retry_at = None
    record.last_error = None
    await db.commit()
    await db.refresh(record)
    return record


async def mark_delivery_failed(
    db: AsyncSession,
    *,
    delivery_id: UUID,
    error_message: str,
    attempt_count: int,
    max_attempts: int = 3,
) -> Optional[EmailDeliveryLog]:
    record = await get_delivery_by_id(db, delivery_id)
    if not record:
        return None

    record.attempt_count = attempt_count
    record.max_attempts = max_attempts
    record.last_error = error_message[:2000]

    if attempt_count >= max_attempts:
        record.status = "failed"
        record.next_retry_at = None
    else:
        record.status = "pending"
        delay_minutes = 2 ** max(0, attempt_count - 1)
        record.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)

    await db.commit()
    await db.refresh(record)
    return record


async def mark_delivery_bounced(
    db: AsyncSession,
    *,
    delivery_id: UUID,
    error_message: str,
    provider: str,
) -> Optional[EmailDeliveryLog]:
    record = await get_delivery_by_id(db, delivery_id)
    if not record:
        return None
    record.status = "bounced"
    record.provider = provider
    record.next_retry_at = None
    record.last_error = error_message[:2000]
    await db.commit()
    await db.refresh(record)
    return record


async def list_due_retries(
    db: AsyncSession,
    *,
    as_of: Optional[datetime] = None,
    limit: int = 100,
) -> list[EmailDeliveryLog]:
    cursor = _to_utc(as_of or datetime.now(timezone.utc))
    result = await db.execute(
        select(EmailDeliveryLog)
        .where(
            and_(
                EmailDeliveryLog.status == "pending",
                EmailDeliveryLog.next_retry_at.is_not(None),
                EmailDeliveryLog.next_retry_at <= cursor,
                EmailDeliveryLog.attempt_count < EmailDeliveryLog.max_attempts,
            )
        )
        .order_by(EmailDeliveryLog.next_retry_at.asc(), EmailDeliveryLog.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_email_history(
    db: AsyncSession,
    org_id: UUID,
    filters: EmailHistoryFilter,
) -> tuple[list[EmailDeliveryLog], int]:
    conditions = [EmailDeliveryLog.org_id == org_id]
    if filters.status:
        status = filters.status.value if hasattr(filters.status, "value") else filters.status
        conditions.append(EmailDeliveryLog.status == status)
    if filters.channel:
        channel = filters.channel.value if hasattr(filters.channel, "value") else filters.channel
        conditions.append(EmailDeliveryLog.channel == channel)
    if filters.from_date:
        conditions.append(EmailDeliveryLog.created_at >= _to_utc(filters.from_date))
    if filters.to_date:
        conditions.append(EmailDeliveryLog.created_at <= _to_utc(filters.to_date))

    count_result = await db.execute(
        select(func.count(EmailDeliveryLog.id)).where(and_(*conditions))
    )
    total_count = count_result.scalar() or 0

    result = await db.execute(
        select(EmailDeliveryLog)
        .where(and_(*conditions))
        .order_by(desc(EmailDeliveryLog.created_at))
        .offset(filters.skip)
        .limit(filters.limit)
    )
    return list(result.scalars().all()), total_count

