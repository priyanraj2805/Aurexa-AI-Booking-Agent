from fastapi import APIRouter
from app.api.v1.endpoints import (
    alerts,
    approvals,
    audit,
    auth,
    billing,
    flags,
    health,
    impersonation,
    incidents,
    lifecycle,
    notifications,
    plans,
    reports,
    security_settings,
    support,
    tenants,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(lifecycle.router, prefix="/lifecycle", tags=["lifecycle"])
api_router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
api_router.include_router(plans.router, prefix="/plans", tags=["plans"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
api_router.include_router(flags.router, prefix="/flags", tags=["flags"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(impersonation.router, prefix="/impersonation", tags=["impersonation"])
api_router.include_router(support.router, prefix="/support", tags=["support"])
api_router.include_router(
    security_settings.router, prefix="/security", tags=["security"]
)
