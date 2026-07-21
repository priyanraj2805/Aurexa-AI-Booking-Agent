from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.crud.monitoring import (
    acknowledge_system_alert,
    get_alert_trends_for_org,
    get_escalation_policy_with_children,
    list_system_alerts,
    upsert_escalation_policy,
)
from app.db.database import get_db
from app.models.user import User
from app.schemas.monitoring import (
    AcknowledgeSystemAlertRequest,
    AlertComponent,
    AlertSeverity,
    AlertTrendResponse,
    EscalationPolicyResponse,
    EscalationPolicyUpdateRequest,
    EscalationRotationWindowResponse,
    EscalationRoutingTargetResponse,
    SystemAlertFilter,
    SystemAlertResponse,
)

router = APIRouter()


@router.get("/alerts", response_model=list[SystemAlertResponse])
async def get_alerts(
    severity: Optional[AlertSeverity] = Query(default=None),
    component: Optional[AlertComponent] = Query(default=None),
    unresolved_only: bool = Query(default=False),
    recent_hours: Optional[int] = Query(default=None, ge=1, le=720),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filters = SystemAlertFilter(
        severity=severity,
        component=component,
        unresolved_only=unresolved_only,
        recent_hours=recent_hours,
        from_date=from_date,
        to_date=to_date,
        skip=skip,
        limit=limit,
    )
    rows, _total = await list_system_alerts(db, org_id=user.org_id, filters=filters)
    return [SystemAlertResponse.model_validate(row) for row in rows]


@router.get("/alerts/trends", response_model=AlertTrendResponse)
async def get_alert_trends(
    window_hours: int = Query(default=24, ge=1, le=720),
    bucket_minutes: int = Query(default=60, ge=5, le=360),
    severity: Optional[AlertSeverity] = Query(default=None),
    component: Optional[AlertComponent] = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_alert_trends_for_org(
        db,
        org_id=user.org_id,
        window_hours=window_hours,
        bucket_minutes=bucket_minutes,
        severity=severity,
        component=component,
    )


@router.get("/escalation-policy", response_model=EscalationPolicyResponse)
async def get_escalation_policy(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    policy, targets, windows = await get_escalation_policy_with_children(db, org_id=user.org_id)
    return EscalationPolicyResponse(
        id=policy.id,
        org_id=policy.org_id,
        routing_targets=[
            EscalationRoutingTargetResponse(
                id=target.id,
                target_type=target.target_type,
                target_value=target.target_value,
                order_index=target.order_index,
            )
            for target in targets
        ],
        rotation_windows=[
            EscalationRotationWindowResponse(
                id=window.id,
                day_of_week=window.day_of_week,
                start_hour_utc=window.start_hour_utc,
                end_hour_utc=window.end_hour_utc,
                order_index=window.order_index,
            )
            for window in windows
        ],
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


@router.put("/escalation-policy", response_model=EscalationPolicyResponse)
async def put_escalation_policy(
    payload: EscalationPolicyUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    policy, targets, windows = await upsert_escalation_policy(
        db,
        org_id=user.org_id,
        payload=payload,
    )
    return EscalationPolicyResponse(
        id=policy.id,
        org_id=policy.org_id,
        routing_targets=[
            EscalationRoutingTargetResponse(
                id=target.id,
                target_type=target.target_type,
                target_value=target.target_value,
                order_index=target.order_index,
            )
            for target in targets
        ],
        rotation_windows=[
            EscalationRotationWindowResponse(
                id=window.id,
                day_of_week=window.day_of_week,
                start_hour_utc=window.start_hour_utc,
                end_hour_utc=window.end_hour_utc,
                order_index=window.order_index,
            )
            for window in windows
        ],
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


@router.post("/alerts/{alert_id}/acknowledge", response_model=SystemAlertResponse)
async def acknowledge_alert(
    alert_id: UUID,
    payload: AcknowledgeSystemAlertRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    alert = await acknowledge_system_alert(
        db,
        alert_id=alert_id,
        org_id=user.org_id,
        acknowledged_by_user_id=user.id,
        note=payload.note,
    )
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return SystemAlertResponse.model_validate(alert)

