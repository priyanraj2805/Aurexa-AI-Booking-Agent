import os
import time
from typing import Optional

from sqlalchemy import text

from app.core.config import settings
from app.core.email_delivery_service import check_email_service_health
from app.db.database import AsyncSessionLocal


async def build_dependency_health_payload(
    *, request_id: Optional[str], trace_id: Optional[str]
) -> dict:
    checks = {
        "database": {"status": "ok", "latency_ms": None, "error": None},
        "llm": {"status": "ok", "latency_ms": None, "error": None},
        "email": {"status": "ok", "latency_ms": None, "error": None},
        "firecrawl": {"status": "ok", "latency_ms": None, "error": None},
        "calendar": {"status": "ok", "latency_ms": None, "error": None},
    }

    db_start = time.perf_counter()
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"]["latency_ms"] = round((time.perf_counter() - db_start) * 1000, 2)
    except Exception as exc:
        checks["database"]["status"] = "down"
        checks["database"]["error"] = str(exc)
        checks["database"]["latency_ms"] = round((time.perf_counter() - db_start) * 1000, 2)

    llm_start = time.perf_counter()
    try:
        if not settings.LLM_API_KEY:
            checks["llm"]["status"] = "degraded"
            checks["llm"]["error"] = "LLM_API_KEY missing"
        checks["llm"]["latency_ms"] = round((time.perf_counter() - llm_start) * 1000, 2)
    except Exception as exc:
        checks["llm"]["status"] = "down"
        checks["llm"]["error"] = str(exc)
        checks["llm"]["latency_ms"] = round((time.perf_counter() - llm_start) * 1000, 2)

    email_start = time.perf_counter()
    email_checks = check_email_service_health()
    checks["email"]["latency_ms"] = round((time.perf_counter() - email_start) * 1000, 2)
    if email_checks["primary"]["status"] == "down" and email_checks["secondary"]["status"] in (
        "down",
        "not_configured",
    ):
        checks["email"]["status"] = "down"
        checks["email"]["error"] = "Primary and fallback providers unavailable"

    firecrawl_start = time.perf_counter()
    if not settings.FIRECRAWL_API_KEY:
        checks["firecrawl"]["status"] = "degraded"
        checks["firecrawl"]["error"] = "FIRECRAWL_API_KEY not set"
    checks["firecrawl"]["latency_ms"] = round((time.perf_counter() - firecrawl_start) * 1000, 2)

    calendar_start = time.perf_counter()
    google_ok = bool(os.getenv("GOOGLE_CLIENT_ID")) and bool(os.getenv("GOOGLE_CLIENT_SECRET"))
    outlook_ok = bool(os.getenv("MICROSOFT_CLIENT_ID")) and bool(
        os.getenv("MICROSOFT_CLIENT_SECRET")
    )
    if not google_ok and not outlook_ok:
        checks["calendar"]["status"] = "degraded"
        checks["calendar"]["error"] = (
            "No calendar OAuth credentials configured (GOOGLE_CLIENT_ID/SECRET or "
            "MICROSOFT_CLIENT_ID/SECRET)"
        )
    checks["calendar"]["latency_ms"] = round((time.perf_counter() - calendar_start) * 1000, 2)

    if any(value["status"] == "down" for value in checks.values()):
        status = "down"
    elif any(value["status"] != "ok" for value in checks.values()):
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "request_id": request_id,
        "trace_id": trace_id,
        "checks": checks,
    }

