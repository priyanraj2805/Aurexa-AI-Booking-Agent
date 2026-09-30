from dotenv import load_dotenv

load_dotenv()  # Must run before any module reads os.getenv()

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from asgi_correlation_id import CorrelationIdMiddleware
from app.core.config import settings
from app.core.limiter import limiter
from app.api.v1.api import api_router
from app.core.errors import add_exception_handlers
from app.core.logging import setup_logging
from app.core.tasks import run_cleanup_loop
from app.core.whatsapp_debouncer import start_debouncer, stop_debouncer
from app.core.health_status import build_dependency_health_payload
from asgi_correlation_id.context import correlation_id

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background tasks on startup, cancel them on shutdown."""
    # Production Razorpay config validation (live mode must be HTTPS etc.)
    try:
        errs = settings.validate_production_razorpay_config()
        if errs:
            import logging as _lg
            _lg.getLogger(__name__).warning("Razorpay production config issues: %s", "; ".join(errs))
    except Exception:
        pass
    cleanup_task = asyncio.create_task(run_cleanup_loop())
    start_debouncer()
    # The WhatsApp dedup map, per-conversation lock and flood window are
    # per-process dicts. That is correct for the single-worker deployment this
    # service is started with, but it would silently weaken if someone adds
    # `workers=`. Say so explicitly instead of inheriting it quietly.
    try:
        from app.core.whatsapp_debouncer import (
            detect_configured_worker_count,
            multi_process_risk_report,
        )

        _report = multi_process_risk_report(
            worker_count=detect_configured_worker_count(),
            acknowledged=os.environ.get("WHATSAPP_MULTI_PROCESS_ACK", "").strip().lower()
            in ("1", "true", "yes"),
        )
        if _report:
            import logging as _lg

            _lg.getLogger(__name__).warning("WHATSAPP MULTI-PROCESS WARNING\n%s", _report)
    except Exception:
        pass
    yield
    cleanup_task.cancel()
    stop_debouncer()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── CSRF Protection Middleware ────────────────────
# Requires "X-Requested-With: XMLHttpRequest" header on all state-changing
# requests that use cookie auth. Browsers won't add this on cross-origin
# form submissions, preventing CSRF attacks.
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {
    "/api/v1/chat/message/dispatch",
    "/api/v1/auth/security/otp/verify",
    "/api/v1/auth/security/otp/generate",
    "/api/v1/auth/otp/verify",
    "/api/v1/auth/otp/generate",
    "/api/v1/widget/client-log",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in CSRF_SAFE_METHODS:
            # Only enforce for cookie-authenticated requests (not API-key auth)
            has_cookie = request.cookies.get("ds_token")
            has_bearer = (request.headers.get("authorization") or "").startswith(
                "Bearer "
            )
            path = request.url.path

            if has_cookie and not has_bearer and path not in CSRF_EXEMPT_PATHS:
                csrf_header = request.headers.get("x-requested-with")
                if csrf_header != "XMLHttpRequest":
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": "CSRF validation failed. Include X-Requested-With: XMLHttpRequest header."
                        },
                    )
        return await call_next(request)


# Set all CORS enabled origins
# allow_origin_regex also permits Cloudflare tunnel URLs (*.trycloudflare.com)
# so the browser doesn't block requests when accessing via public tunnel URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://localhost:3000",
        "http://localhost:3002",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:8005",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:8005",
        "https://dotstark.tech",
        "https://www.dotstark.tech",
    ],
    # Allows any Cloudflare tunnel URL + production domain dotstark.tech
    allow_origin_regex=r"https://.*\.(trycloudflare\.com|cfargotunnel\.com|dotstark\.tech)",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Org-API-Key",
        "X-Widget-Token",
        "X-Requested-With",
        "X-Request-ID",
    ],
)

app.add_middleware(CorrelationIdMiddleware)

# CSRF middleware must be added AFTER CORS (inner middleware runs first)
app.add_middleware(CSRFMiddleware)

add_exception_handlers(app)


@app.get("/health")
async def health_check(request: Request):
    request_id = correlation_id.get() or request.headers.get("x-request-id")
    trace_id = request.headers.get("x-trace-id")
    return await build_dependency_health_payload(request_id=request_id, trace_id=trace_id)


app.include_router(api_router, prefix=settings.API_V1_STR)

# ── Serve widget bundle at /static/widget/ ────────────────────
# Tenants embed: <script src="https://your-domain/static/widget/widget.js"></script>
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "widget"
if STATIC_DIR.is_dir():
    app.mount(
        "/static/widget", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="widget-static"
    )
