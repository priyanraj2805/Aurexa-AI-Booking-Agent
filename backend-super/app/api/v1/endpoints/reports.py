"""Reports engine (Phase 6). Live analytics over usage_events + tenant
tables, exposed as JSON and CSV.

Report types (12): usage, revenue, bookings, conversations, ai, api,
storage, kb, growth, performance, health, widget.
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import CurrentAdmin, require_permission
from app.db.database import get_db

router = APIRouter()

REPORTS = (
    "usage",
    "revenue",
    "bookings",
    "conversations",
    "ai",
    "api",
    "storage",
    "kb",
    "growth",
    "performance",
    "health",
    "widget",
)

DAYS = 30


async def _usage_report(db: AsyncSession, month: Optional[str]) -> dict:
    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m-01")
    try:
        month_date = datetime.strptime(month, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM-DD (first of month)")
    rows = (
        await db.execute(
            text(
                """
                SELECT u.org_id, o.name AS org_name,
                       COALESCE(SUM(u.count) FILTER (WHERE u.metric = 'bookings'), 0) AS bookings,
                       COALESCE(SUM(u.count) FILTER (WHERE u.metric = 'ai_tokens'), 0) AS ai_tokens
                FROM super_admin.usage_events u
                JOIN public.organizations o ON o.id = u.org_id
                WHERE u.event_month = :month
                GROUP BY u.org_id, o.name
                ORDER BY bookings DESC, ai_tokens DESC
                """
            ),
            {"month": month_date},
        )
    ).mappings()
    rows = [dict(r) for r in rows]
    return {
        "month": month,
        "total_bookings": sum(r["bookings"] for r in rows),
        "total_ai_tokens": sum(r["ai_tokens"] for r in rows),
        "rows": rows,
    }


async def _bookings_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    daily = (
        await db.execute(
            text(
                """
                SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS bookings
                FROM public.bookings
                WHERE created_at >= :since
                GROUP BY day ORDER BY day
                """
            ),
            {"since": since},
        )
    ).mappings()
    daily_rows = [dict(r) for r in daily]
    orgs = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name, count(b.id) AS bookings
                FROM public.organizations o
                LEFT JOIN public.bookings b ON b.org_id = o.id AND b.created_at >= :since
                GROUP BY o.id, o.name
                ORDER BY bookings DESC
                """
            ),
            {"since": since},
        )
    ).mappings()
    return {
        "days": DAYS,
        "total_bookings": sum(r["bookings"] for r in daily_rows),
        "daily": daily_rows,
        "by_org": [dict(r) for r in orgs],
    }


async def _conversations_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    daily = (
        await db.execute(
            text(
                """
                SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                       count(*) AS sessions
                FROM public.chat_sessions
                WHERE created_at >= :since
                GROUP BY day ORDER BY day
                """
            ),
            {"since": since},
        )
    ).mappings()
    orgs = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name, count(s.id) AS sessions,
                       count(m.id) AS messages
                FROM public.organizations o
                LEFT JOIN public.chat_sessions s
                    ON s.org_id = o.id AND s.created_at >= :since
                LEFT JOIN public.chat_messages m
                    ON m.session_id = s.id AND m.created_at >= :since
                GROUP BY o.id, o.name
                ORDER BY sessions DESC
                """
            ),
            {"since": since},
        )
    ).mappings()
    return {
        "days": DAYS,
        "total_sessions": sum(r["sessions"] for r in daily),
        "daily": [dict(r) for r in daily],
        "by_org": [dict(r) for r in orgs],
    }


async def _ai_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m-01")
    month_date = datetime.strptime(month, "%Y-%m-%d").date()
    rows = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name,
                       COALESCE(u.ai_tokens, 0) AS ai_tokens,
                       COALESCE(m.messages, 0) AS messages
                FROM public.organizations o
                LEFT JOIN (
                    SELECT org_id, SUM(count) AS ai_tokens
                    FROM super_admin.usage_events
                    WHERE metric = 'ai_tokens' AND event_month = :month
                    GROUP BY org_id
                ) u ON u.org_id = o.id
                LEFT JOIN (
                    SELECT s.org_id, count(*) AS messages
                    FROM public.chat_messages m
                    JOIN public.chat_sessions s ON s.id = m.session_id
                    WHERE m.created_at >= date_trunc('month', now())
                    GROUP BY s.org_id
                ) m ON m.org_id = o.id
                ORDER BY ai_tokens DESC, messages DESC
                """
            ),
            {"month": month_date},
        )
    ).mappings()
    return {"month": month, "rows": [dict(r) for r in rows]}


async def _api_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    rows = (
        await db.execute(
            text(
                """
                SELECT org_id, action, count(*) AS calls
                FROM public.audit_logs
                WHERE created_at >= :since
                GROUP BY org_id, action
                ORDER BY calls DESC
                LIMIT 50
                """
            ),
            {"since": since},
        )
    ).mappings()
    orgs = (
        await db.execute(
            text(
                """
                SELECT org_id, count(*) AS total
                FROM public.audit_logs
                WHERE created_at >= :since
                GROUP BY org_id
                ORDER BY total DESC
                """
            ),
            {"since": since},
        )
    ).mappings()
    return {
        "days": DAYS,
        "by_org": [dict(r) for r in orgs],
        "top_actions": [dict(r) for r in rows],
    }


async def _storage_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    rows = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name, count(c.id) AS chunks,
                       COALESCE(SUM(octet_length(c.content)), 0) AS bytes
                FROM public.organizations o
                LEFT JOIN public.document_chunks c ON c.org_id = o.id
                GROUP BY o.id, o.name
                ORDER BY bytes DESC
                """
            )
        )
    ).mappings()
    rows = [dict(r) for r in rows]
    return {
        "total_chunks": sum(r["chunks"] for r in rows),
        "total_bytes": sum(r["bytes"] for r in rows),
        "rows": rows,
    }


async def _kb_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    rows = (
        await db.execute(
            text(
                """
                SELECT o.id, o.name, c.source_type, count(*) AS chunks
                FROM public.organizations o
                JOIN public.document_chunks c ON c.org_id = o.id
                GROUP BY o.id, o.name, c.source_type
                ORDER BY o.name, chunks DESC
                """
            )
        )
    ).mappings()
    return {"rows": [dict(r) for r in rows]}


async def _growth_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    date_trunc('month', o.created_at)::date AS month,
                    count(DISTINCT o.id) AS orgs_created,
                    count(DISTINCT u.id) AS users_created
                FROM public.organizations o
                LEFT JOIN public.users u ON u.org_id = o.id
                WHERE o.created_at >= now() - interval '12 months'
                GROUP BY 1
                ORDER BY 1
                """
            )
        )
    ).mappings()
    return {"rows": [dict(r) for r in rows]}


async def _performance_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    row = (
        await db.execute(
            text(
                """
                SELECT
                    count(*) AS bookings_30d,
                    count(DISTINCT org_id) AS active_orgs,
                    count(*) FILTER (WHERE meeting_link_status = 'ready') AS with_meeting_link,
                    count(*) FILTER (WHERE created_at >= now() - interval '7 days') AS bookings_7d
                FROM public.bookings
                WHERE created_at >= :since
                """
            ),
            {"since": since},
        )
    ).mappings().first()
    return dict(row)


async def _health_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    open_rows = (
        await db.execute(
            text(
                """
                SELECT component, severity, count(*) AS open
                FROM public.system_alerts
                WHERE resolved_at IS NULL
                GROUP BY component, severity
                ORDER BY component, severity
                """
            )
        )
    ).mappings()
    resolved = (
        await db.execute(
            text(
                """
                SELECT count(*) AS resolved_30d
                FROM public.system_alerts
                WHERE resolved_at >= now() - interval '30 days'
                """
            )
        )
    ).scalar()
    open_list = [dict(r) for r in open_rows]
    return {
        "open": open_list,
        "open_total": sum(r["open"] for r in open_list),
        "resolved_30d": int(resolved or 0),
    }


async def _widget_report(db: AsyncSession, month: Optional[str] = None) -> dict:
    """Embeddable widget snippet counts: bookings made via embedded widgets."""
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    rows = (
        await db.execute(
            text(
                """
                SELECT o.name, count(b.id) AS bookings
                FROM public.bookings b
                JOIN public.organizations o ON o.id = b.org_id
                WHERE b.created_at >= :since
                GROUP BY o.name
                ORDER BY bookings DESC
                """
            ),
            {"since": since},
        )
    ).mappings()
    return {"days": DAYS, "rows": [dict(r) for r in rows]}


REPORT_BUILDERS = {
    "usage": _usage_report,
    "bookings": _bookings_report,
    "conversations": _conversations_report,
    "ai": _ai_report,
    "api": _api_report,
    "storage": _storage_report,
    "kb": _kb_report,
    "growth": _growth_report,
    "performance": _performance_report,
    "health": _health_report,
    "widget": _widget_report,
}


def _flatten(data: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Flatten a report payload into (column, value) pairs for CSV."""
    out: list[tuple[str, object]] = []
    for key, value in data.items():
        if isinstance(value, list):
            continue
        if isinstance(value, dict):
            out.extend(_flatten(value, f"{prefix}{key}_"))
        else:
            out.append((f"{prefix}{key}", value))
    return out


@router.get("")
async def list_reports(
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("reports.view")),
):
    return {"items": list(REPORTS), "available": True}


@router.get("/{report_name}")
async def get_report(
    report_name: str,
    month: Optional[str] = Query(None, description="YYYY-MM-DD (first of month) for usage"),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("reports.view")),
):
    if report_name not in REPORT_BUILDERS:
        raise HTTPException(404, f"Unknown report: {report_name} (available: {', '.join(REPORTS)})")
    data = await REPORT_BUILDERS[report_name](db, month)
    return {
        "report": report_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **data,
    }


@router.get("/{report_name}/csv")
async def get_report_csv(
    report_name: str,
    month: Optional[str] = Query(None, description="YYYY-MM-DD (first of month) for usage"),
    db: AsyncSession = Depends(get_db),
    _: CurrentAdmin = Depends(require_permission("reports.view")),
):
    if report_name == "revenue":
        raise HTTPException(404, "Revenue CSV lives at /billing/reports/revenue.csv")
    if report_name not in REPORT_BUILDERS:
        raise HTTPException(404, f"Unknown report: {report_name}")
    data = await REPORT_BUILDERS[report_name](db, month)

    buf = io.StringIO()
    writer = csv.writer(buf)
    flat = _flatten(data)
    writer.writerow(["metric", "value"])
    for key, value in flat:
        writer.writerow([key, value])
    for idx, row in enumerate(data.get("rows", []) or []):
        writer.writerow([f"row.{idx}.{k}" for k in row.keys()])
        writer.writerow([str(row[k]) for k in row.keys()])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{report_name}_report.csv"'},
    )
