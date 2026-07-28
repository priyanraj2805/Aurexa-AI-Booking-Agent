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
    slots_bulk,
    integrations,
    documents,
    firecrawl_webhook,
)

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
api_router.include_router(
    integrations.router, prefix="/integrations", tags=["integrations"]
)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(firecrawl_webhook.router, prefix="/firecrawl", tags=["firecrawl"])
