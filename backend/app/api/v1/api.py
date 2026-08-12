from fastapi import APIRouter
from app.api.v1.endpoints import (
    organizations,
    chat,
    otp,
    slots,
    auth,
    dashboard,
    audit,
    email,
    monitoring,
    whatsapp,
    widget,
    widget_ws,
    slots_bulk,
    integrations,
    documents,
    firecrawl_webhook,
    support_requests,
)
from app.core.config import settings
from app.api.v1.endpoints.internal import bridge as internal_bridge

api_router = APIRouter()
api_router.include_router(organizations.router, prefix="/organizations", tags=["admin"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(otp.router, prefix="/auth/security/otp", tags=["auth"])
# Backward-compatible alias for legacy widget clients.
api_router.include_router(otp.router, prefix="/auth/otp", tags=["auth"])
api_router.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
api_router.include_router(slots.router, prefix="/slots", tags=["slots"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
api_router.include_router(email.router, prefix="/email", tags=["email"])
api_router.include_router(monitoring.router, prefix="/monitoring", tags=["monitoring"])
api_router.include_router(slots_bulk.router, prefix="/slots-bulk", tags=["slots_bulk"])
api_router.include_router(widget.router, prefix="/widget", tags=["widget"])
api_router.include_router(widget_ws.router, tags=["widget-ws"])
api_router.include_router(
    integrations.router, prefix="/integrations", tags=["integrations"]
)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(firecrawl_webhook.router, prefix="/firecrawl", tags=["firecrawl"])
api_router.include_router(
    support_requests.router, prefix="/support", tags=["support"]
)

# Super Admin internal bridge — registered ONLY when the env gate is on.
# (verify_bridge additionally 404s while disabled, so this is doubly inert.)
if settings.INTERNAL_BRIDGE_ENABLED:
    api_router.include_router(
        internal_bridge.router,
        prefix=internal_bridge.BRIDGE_PREFIX,
        tags=["internal-bridge"],
    )
