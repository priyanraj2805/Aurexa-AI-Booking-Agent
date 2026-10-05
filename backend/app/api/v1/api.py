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
    whatsapp_qr,
    whatsapp_webhook,
    widget,
    widget_ws,
    slots_bulk,
    integrations,
    documents,
    firecrawl_webhook,
    support_requests,
    broadcasts,
    account,
    notifications,
    razorpay,
    razorpay_route,
    catalog,
)
from app.core.config import settings
from app.api.v1.endpoints.internal import bridge as internal_bridge

api_router = APIRouter()
api_router.include_router(organizations.router, prefix="/organizations", tags=["admin"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(otp.router, prefix="/auth/security/otp", tags=["auth"])
# Backward-compatible alias for legacy widget clients.
api_router.include_router(otp.router, prefix="/auth/otp", tags=["auth"])
api_router.include_router(whatsapp_qr.router, prefix="", tags=["whatsapp-qr"])
api_router.include_router(whatsapp_webhook.router, prefix="/whatsapp", tags=["whatsapp-webhook"])
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
api_router.include_router(broadcasts.router, prefix="/broadcasts", tags=["broadcasts"])
api_router.include_router(account.router, prefix="/account", tags=["account"])
api_router.include_router(
    notifications.router, prefix="/dashboard/notifications", tags=["notifications"]
)

# Service catalog: generic units / providers / fees (org-scoped CRUD)
api_router.include_router(catalog.router, prefix="/catalog", tags=["catalog"])

# Razorpay Route payments + provider payouts (always mounted; routes themselves enforce auth / org scope)
api_router.include_router(razorpay.router, prefix="", tags=["razorpay"])

# Razorpay Route provider payouts (additive; each route 404s unless RAZORPAY_ROUTE_ENABLED)
api_router.include_router(razorpay_route.router, prefix="", tags=["razorpay-route"])

# Super Admin internal bridge — registered ONLY when the env gate is on.
# (verify_bridge additionally 404s while disabled, so this is doubly inert.)
if settings.INTERNAL_BRIDGE_ENABLED:
    api_router.include_router(
        internal_bridge.router,
        prefix=internal_bridge.BRIDGE_PREFIX,
        tags=["internal-bridge"],
    )
