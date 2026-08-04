from dotenv import load_dotenv

load_dotenv()  # Must run before any module reads os.getenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.limiter import limiter
from app.db.database import engine, readonly_engine
from app.api.v1.api import api_router


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://localhost:3003",
        "http://127.0.0.1:3003",
        "http://localhost:8006",
        "http://127.0.0.1:8006",
    ],
    allow_origin_regex=r"https://.*\.(trycloudflare\.com|cfargotunnel\.com)",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "X-Request-ID",
    ],
)


# ── CSRF Protection Middleware ────────────────────
# Requires "X-Requested-With: XMLHttpRequest" on state-changing requests that
# use the super_admin_token cookie. Browsers don't add this header on
# cross-origin form submissions, preventing CSRF attacks.
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in CSRF_SAFE_METHODS:
            has_cookie = request.cookies.get(settings.SUPER_ADMIN_COOKIE_NAME)
            has_bearer = (request.headers.get("authorization") or "").startswith(
                "Bearer "
            )
            if has_cookie and not has_bearer:
                csrf_header = request.headers.get("x-requested-with")
                if csrf_header != "XMLHttpRequest":
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": "CSRF validation failed. Include X-Requested-With: XMLHttpRequest header."
                        },
                    )
        return await call_next(request)


# CSRF middleware must be added AFTER CORS (inner middleware runs first)
app.add_middleware(CSRFMiddleware)


@app.get("/health")
async def health_check(request: Request):
    """Pings both DB pools (admin + read-only) and reports gate states."""
    async def ping(pool_name: str, eng) -> str:
        try:
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return "ok"
        except Exception as exc:
            return f"error: {type(exc).__name__}"

    admin_db = await ping("admin", engine)
    readonly_db = await ping("readonly", readonly_engine)

    return {
        "status": "ok" if admin_db == "ok" and readonly_db == "ok" else "degraded",
        "admin_db": admin_db,
        "readonly_db": readonly_db,
        "bridge_enabled": settings.INTERNAL_BRIDGE_ENABLED,
        "telemetry_enabled": settings.TELEMETRY_ENABLED,
    }


app.include_router(api_router, prefix=settings.API_V1_STR)
