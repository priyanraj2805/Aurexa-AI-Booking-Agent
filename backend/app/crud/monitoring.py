from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.escalation_policy import (
    EscalationPolicy,
    EscalationRoutingTarget,
    EscalationRotationWindow,
)
from app.models.system_alert import SystemAlert
from app.schemas.monitoring import (
    AlertTrendAnomaly,
    AlertTrendBucket,
    AlertTrendResponse,
    AlertTrendSummary,
    AlertComponent,
    AlertSeverity,
    EscalationPolicyUpdateRequest,
    SystemAlertCreate,
    SystemAlertFilter,
)


def build_alert_dedupe_key(
    *,
    org_id: Optional[UUID],
    component: str,
    code: str,
) -> str:
    org_key = str(org_id) if org_id else "global"
    return f"{org_key}:{component}:{code}"


def _sanitize_alert_context(ctx: dict | None) -> dict | None:
    if not isinstance(ctx, dict):
        return ctx
    sanitized: dict = {}
    for k, v in ctx.items():
        lk = str(k).lower()
        # PII guardrail: never store email/phone/token/name/http in alert context
        if lk in {"email", "phone", "phone_number", "customer_email", "admin_email", "recipient_email", "name", "token", "authorization", "api_key", "password"}:
            continue
        if "email" in lk or "phone" in lk or "token" in lk or lk == "http" or "url" in lk:
            # Allow meeting_link_status/provider/error_code but not raw URLs/tokens
            if lk in {"provider", "meeting_link_status", "error_code", "failure_type", "meeting_link_status"}:
                sanitized[k] = v
                continue
            continue
        # Also strip http-like values
        if isinstance(v, str) and ("http://" in v or "https://" in v):
            continue
        sanitized[k] = v
    return sanitized


async def create_system_alert(db: AsyncSession, payload: SystemAlertCreate) -> SystemAlert:
    component = (
        payload.component.value if hasattr(payload.component, "value") else payload.component
    )
    severity = payload.severity.value if hasattr(payload.severity, "value") else payload.severity
    # PII guardrail: sanitize context before dedupe/save
    if payload.context is not None:
        payload.context = _sanitize_alert_context(payload.context)
    dedupe_key = payload.dedupe_key or build_alert_dedupe_key(
        org_id=payload.org_id,
        component=component,
        code=payload.code,
    )
    existing_result = await db.execute(
        select(SystemAlert).where(
            and_(SystemAlert.dedupe_key == dedupe_key, SystemAlert.resolved_at.is_(None))
        )
    )
    existing = existing_result.scalars().first()
    if existing:
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.occurrence_count = (existing.occurrence_count or 1) + 1
        existing.context = payload.context
        existing.request_id = payload.request_id
        existing.trace_id = payload.trace_id
        await db.commit()
        await db.refresh(existing)
        return existing

    alert = SystemAlert(
        org_id=payload.org_id,
        component=component,
        severity=severity,
        code=payload.code,
        dedupe_key=dedupe_key,
        message=payload.message,
        request_id=payload.request_id,
        trace_id=payload.trace_id,
        context=payload.context,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def list_system_alerts(
    db: AsyncSession,
    *,
    org_id: Optional[UUID],
    filters: SystemAlertFilter,
) -> tuple[list[SystemAlert], int]:
    now_utc = datetime.now(timezone.utc)
    conditions = []
    if org_id is not None:
        conditions.append(or_(SystemAlert.org_id == org_id, SystemAlert.org_id.is_(None)))
    if filters.severity:
        severity = filters.severity.value if hasattr(filters.severity, "value") else filters.severity
        conditions.append(SystemAlert.severity == severity)
    if filters.component:
        component = filters.component.value if hasattr(filters.component, "value") else filters.component
        conditions.append(SystemAlert.component == component)
    if filters.from_date:
        conditions.append(SystemAlert.created_at >= filters.from_date)
    if filters.to_date:
        conditions.append(SystemAlert.created_at <= filters.to_date)
    if filters.unresolved_only:
        conditions.append(SystemAlert.resolved_at.is_(None))
    if filters.recent_hours is not None:
        conditions.append(SystemAlert.last_seen_at >= now_utc - timedelta(hours=filters.recent_hours))

    where_clause = and_(*conditions) if conditions else True

    count_result = await db.execute(select(func.count(SystemAlert.id)).where(where_clause))
    total_count = count_result.scalar() or 0

    result = await db.execute(
        select(SystemAlert)
        .where(where_clause)
        .order_by(SystemAlert.resolved_at.is_not(None).asc(), desc(SystemAlert.last_seen_at))
        .offset(filters.skip)
        .limit(filters.limit)
    )
    return list(result.scalars().all()), total_count


async def get_or_create_escalation_policy(
    db: AsyncSession,
    *,
    org_id: UUID,
) -> EscalationPolicy:
    result = await db.execute(select(EscalationPolicy).where(EscalationPolicy.org_id == org_id))
    policy = result.scalars().first()
    if policy:
        return policy

    policy = EscalationPolicy(org_id=org_id)
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def get_escalation_policy_with_children(
    db: AsyncSession,
    *,
    org_id: UUID,
) -> tuple[EscalationPolicy, list[EscalationRoutingTarget], list[EscalationRotationWindow]]:
    policy = await get_or_create_escalation_policy(db, org_id=org_id)

    targets_result = await db.execute(
        select(EscalationRoutingTarget)
        .where(EscalationRoutingTarget.policy_id == policy.id)
        .order_by(EscalationRoutingTarget.order_index.asc())
    )
    windows_result = await db.execute(
        select(EscalationRotationWindow)
        .where(EscalationRotationWindow.policy_id == policy.id)
        .order_by(EscalationRotationWindow.order_index.asc())
    )
    return policy, list(targets_result.scalars().all()), list(windows_result.scalars().all())


async def upsert_escalation_policy(
    db: AsyncSession,
    *,
    org_id: UUID,
    payload: EscalationPolicyUpdateRequest,
) -> tuple[EscalationPolicy, list[EscalationRoutingTarget], list[EscalationRotationWindow]]:
    policy = await get_or_create_escalation_policy(db, org_id=org_id)

    await db.execute(
        EscalationRoutingTarget.__table__.delete().where(
            EscalationRoutingTarget.policy_id == policy.id
        )
    )
    await db.execute(
        EscalationRotationWindow.__table__.delete().where(
            EscalationRotationWindow.policy_id == policy.id
        )
    )

    targets: list[EscalationRoutingTarget] = []
    windows: list[EscalationRotationWindow] = []
    for idx, item in enumerate(payload.routing_targets):
        target = EscalationRoutingTarget(
            policy_id=policy.id,
            target_type=item.target_type.strip(),
            target_value=item.target_value.strip(),
            order_index=idx,
        )
        db.add(target)
        targets.append(target)

    for idx, item in enumerate(payload.rotation_windows):
        window = EscalationRotationWindow(
            policy_id=policy.id,
            day_of_week=item.day_of_week,
            start_hour_utc=item.start_hour_utc,
            end_hour_utc=item.end_hour_utc,
            order_index=idx,
        )
        db.add(window)
        windows.append(window)

    policy.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(policy)
    for target in targets:
        await db.refresh(target)
    for window in windows:
        await db.refresh(window)
    return policy, targets, windows


async def acknowledge_system_alert(
    db: AsyncSession,
    *,
    alert_id: UUID,
    org_id: UUID,
    acknowledged_by_user_id: UUID,
    note: Optional[str],
) -> Optional[SystemAlert]:
    result = await db.execute(
        select(SystemAlert).where(
            and_(
                SystemAlert.id == alert_id,
                or_(SystemAlert.org_id == org_id, SystemAlert.org_id.is_(None)),
            )
        )
    )
    alert = result.scalars().first()
    if alert is None:
        return None

    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by_user_id = acknowledged_by_user_id
    alert.acknowledgement_note = note.strip() if note else None
    await db.commit()
    await db.refresh(alert)
    return alert


def _bucket_floor(dt: datetime, bucket_minutes: int) -> datetime:
    dt_utc = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    floored_minute = (dt_utc.minute // bucket_minutes) * bucket_minutes
    return dt_utc.replace(minute=floored_minute)


def _compute_anomaly(current_total: int, baseline_avg: float) -> AlertTrendAnomaly:
    min_count_floor = 3
    spike_multiplier = 2.0
    if baseline_avg <= 0:
        return AlertTrendAnomaly(is_anomaly=False, score=0.0, reason=None)
    score = current_total / baseline_avg
    is_anomaly = current_total >= min_count_floor and score >= spike_multiplier
    return AlertTrendAnomaly(
        is_anomaly=is_anomaly,
        score=round(score, 2),
        reason="spike_vs_baseline" if is_anomaly else None,
    )


async def get_alert_trends_for_org(
    db: AsyncSession,
    *,
    org_id: UUID,
    window_hours: int,
    bucket_minutes: int,
    severity: Optional[AlertSeverity] = None,
    component: Optional[AlertComponent] = None,
) -> AlertTrendResponse:
    now_utc = datetime.now(timezone.utc)
    start_time = now_utc - timedelta(hours=window_hours)

    conditions = [
        or_(SystemAlert.org_id == org_id, SystemAlert.org_id.is_(None)),
        SystemAlert.last_seen_at >= start_time,
    ]
    if severity is not None:
        severity_value = severity.value if hasattr(severity, "value") else severity
        conditions.append(SystemAlert.severity == severity_value)
    if component is not None:
        component_value = component.value if hasattr(component, "value") else component
        conditions.append(SystemAlert.component == component_value)

    rows_result = await db.execute(
        select(
            SystemAlert.last_seen_at,
            SystemAlert.severity,
            SystemAlert.resolved_at,
        ).where(and_(*conditions))
    )
    rows = rows_result.all()

    bucket_map: dict[datetime, dict[str, int]] = {}
    for last_seen_at, sev, resolved_at in rows:
        if last_seen_at is None:
            continue
        bucket_start = _bucket_floor(last_seen_at, bucket_minutes)
        if bucket_start not in bucket_map:
            bucket_map[bucket_start] = {
                "total_alerts": 0,
                "critical_alerts": 0,
                "unresolved_alerts": 0,
            }
        bucket_map[bucket_start]["total_alerts"] += 1
        if sev == "critical":
            bucket_map[bucket_start]["critical_alerts"] += 1
        if resolved_at is None:
            bucket_map[bucket_start]["unresolved_alerts"] += 1

    series: list[AlertTrendBucket] = []
    if bucket_map:
        baseline_avg = sum(v["total_alerts"] for v in bucket_map.values()) / len(bucket_map)
        for bucket_start in sorted(bucket_map.keys()):
            counts = bucket_map[bucket_start]
            anomaly = _compute_anomaly(counts["total_alerts"], baseline_avg)
            series.append(
                AlertTrendBucket(
                    bucket_start=bucket_start,
                    total_alerts=counts["total_alerts"],
                    critical_alerts=counts["critical_alerts"],
                    unresolved_alerts=counts["unresolved_alerts"],
                    anomaly=anomaly,
                )
            )
    else:
        baseline_avg = 0.0

    latest = series[-1] if series else None
    summary = AlertTrendSummary(
        latest_total=latest.total_alerts if latest else 0,
        latest_critical=latest.critical_alerts if latest else 0,
        latest_unresolved=latest.unresolved_alerts if latest else 0,
        baseline_avg_total=round(baseline_avg, 2),
        anomaly_buckets_count=sum(1 for row in series if row.anomaly.is_anomaly),
    )

    return AlertTrendResponse(
        window_hours=window_hours,
        bucket_minutes=bucket_minutes,
        series=series,
        summary=summary,
    )

