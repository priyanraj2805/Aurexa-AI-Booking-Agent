from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
import logging

from app.db.database import AsyncSessionLocal
from app.crud.monitoring import create_system_alert
from app.schemas.monitoring import AlertComponent, AlertSeverity, SystemAlertCreate
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
    BadRequestException,
    ConcurrencyException
)

logger = logging.getLogger(__name__)

def add_exception_handlers(app: FastAPI):
    
    @app.exception_handler(NotFoundException)
    async def not_found_handler(request: Request, exc: NotFoundException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "NOT_FOUND", "message": exc.detail}},
        )

    @app.exception_handler(ForbiddenException)
    async def forbidden_handler(request: Request, exc: ForbiddenException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "FORBIDDEN", "message": exc.detail}},
        )

    @app.exception_handler(BadRequestException)
    async def bad_request_handler(request: Request, exc: BadRequestException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "BAD_REQUEST", "message": exc.detail}},
        )

    @app.exception_handler(ConcurrencyException)
    async def concurrency_handler(request: Request, exc: ConcurrencyException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "CONCURRENCY_CONFLICT", "message": exc.detail}},
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_handler(request: Request, exc: SQLAlchemyError):
        logger.error(f"Database error: {str(exc)}")
        request_id = request.headers.get("x-request-id")
        trace_id = request.headers.get("x-trace-id")
        try:
            async with AsyncSessionLocal() as db:
                await create_system_alert(
                    db,
                    SystemAlertCreate(
                        component=AlertComponent.DATABASE,
                        severity=AlertSeverity.CRITICAL,
                        code="DATABASE_ERROR",
                        message="Runtime database exception raised",
                        request_id=request_id,
                        trace_id=trace_id,
                        context={
                            "path": request.url.path,
                            "method": request.method,
                            "error": str(exc),
                        },
                    ),
                )
        except Exception as alert_err:
            logger.warning("database.alert.emit.failed: %s", alert_err)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "DATABASE_ERROR", "message": "An internal database error occurred."}},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled exception: {str(exc)}")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}},
        )
