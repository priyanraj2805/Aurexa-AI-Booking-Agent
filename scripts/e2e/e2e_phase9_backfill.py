"""Phase 9.5 - usage_events backfill for the Super Admin control plane.

Reprojects historical usage into super_admin.usage_events (monthly
partitions) straight from the source tables, so the Phase 6 usage report and
Phase 5 plan-metering show real numbers instead of zeros for months before
telemetry was enabled (or while TELEMETRY_ENABLED=false).

Metrics backfilled (absolute per-org-per-month counts, UPSERT overwrite):
  bookings          <- public.bookings.created_at
  org.create        <- public.audit_logs (action = 'org.create')
  org.suspend       <- public.audit_logs (action = 'org.suspend')
  org.restore       <- public.audit_logs (action = 'org.restore')
  org.delete        <- public.audit_logs (action = 'org.delete')
  user.disable      <- public.audit_logs (action = 'user.disable')
  user.enable       <- public.audit_logs (action = 'user.enable')
  impersonation.start <- public.audit_logs (action = 'impersonation.start')

NOT backfilled: ai_tokens — no per-message token counts exist anywhere in
the tenant DB (chat_messages.metadata only carries intent/directive), so
those can only accumulate going forward. The report treats missing
ai_tokens as 0, which is safe.

Idempotent: re-running overwrites the same (event_month, org_id, metric)
cells with fresh absolute counts. Partitions are auto-created by the
super_admin.ensure_usage_partition trigger.

Prereqs: Postgres up, dotstark_v2 reachable with postgres/dotstark_secure_pass.
"""
import asyncio
from datetime import timedelta

import asyncpg

DB_DSN = "postgresql://postgres:dotstark_secure_pass@localhost:5432/dotstark_v2"

SOURCE_QUERIES = {
    # (event_month, org_id, metric) -> count, derived from ...
    "bookings": """
        SELECT date_trunc('month', created_at)::date AS event_month,
               org_id,
               'bookings' AS metric,
               count(*) AS cnt
        FROM public.bookings
        GROUP BY 1, 2
    """,
    "lifecycle": """
        SELECT date_trunc('month', created_at)::date AS event_month,
               org_id,
               action AS metric,
               count(*) AS cnt
        FROM public.audit_logs
        WHERE action IN ('org.create', 'org.suspend', 'org.restore', 'org.delete',
                         'user.disable', 'user.enable', 'impersonation.start')
        GROUP BY 1, 2, 3
    """,
}

UPSERT_SQL = """
    INSERT INTO super_admin.usage_events (event_month, org_id, metric, count, updated_at)
    VALUES ($1::date, $2::uuid, $3, $4, now())
    ON CONFLICT (event_month, org_id, metric)
    DO UPDATE SET count = EXCLUDED.count, updated_at = now()
"""

CREATE_PARTITION_SQL = """
    CREATE TABLE IF NOT EXISTS super_admin.usage_events_{name}
    PARTITION OF super_admin.usage_events
    FOR VALUES FROM ('{start}') TO ('{end}')
"""


async def ensure_partitions(conn, months: set) -> None:
    """Create monthly partitions for every month that has source data.

    The DB's BEFORE INSERT trigger (ensure_usage_partition) cannot help with
    past months: partition routing happens before row triggers fire, so a
    missing partition aborts the INSERT even though the trigger would have
    created it. telemetry.py never hits this because it only writes the
    current month (created eagerly by the migration).
    """
    for month in sorted(months):
        start = month
        end = (month + timedelta(days=32)).replace(day=1)
        name = month.strftime("%Y_%m")
        await conn.execute(
            CREATE_PARTITION_SQL.format(name=name, start=start.isoformat(), end=end.isoformat())
        )
        print(f"    ensured partition usage_events_{name}")


async def main() -> int:
    conn = await asyncpg.connect(DB_DSN)
    total_rows = 0
    try:
        months: set = set()
        for source, sql in SOURCE_QUERIES.items():
            rows = await conn.fetch(sql)
            print(f"--- {source}: {len(rows)} derived rows ---")
            by_metric = {}
            for r in rows:
                months.add(r["event_month"])
                by_metric[r["metric"]] = by_metric.get(r["metric"], 0) + int(r["cnt"])
            for metric, cnt in sorted(by_metric.items()):
                print(f"    {metric}: {cnt}")
        await ensure_partitions(conn, months)
        for source, sql in SOURCE_QUERIES.items():
            rows = await conn.fetch(sql)
            for r in rows:
                await conn.execute(
                    UPSERT_SQL,
                    r["event_month"],
                    r["org_id"],
                    r["metric"],
                    r["cnt"],
                )
                total_rows += 1

        # ── Verification: every derived cell matches source truth exactly ──
        print("\n--- verification ---")
        stored = await conn.fetch(
            """
            SELECT metric, sum(count) AS cnt, count(DISTINCT event_month) AS months
            FROM super_admin.usage_events
            GROUP BY metric ORDER BY metric
            """
        )
        for r in stored:
            print(f"    stored {r['metric']}: {r['cnt']} over {r['months']} month(s)")

        mismatches = 0
        for source, sql in SOURCE_QUERIES.items():
            rows = await conn.fetch(sql)
            for r in rows:
                stored_cnt = await conn.fetchval(
                    "SELECT count FROM super_admin.usage_events "
                    "WHERE event_month = $1 AND org_id = $2 AND metric = $3",
                    r["event_month"], r["org_id"], r["metric"],
                )
                if int(stored_cnt or 0) != int(r["cnt"]):
                    mismatches += 1
                    print(
                        f"    MISMATCH {r['metric']} {r['event_month']} "
                        f"{r['org_id']}: stored={stored_cnt} source={r['cnt']}"
                    )
        ok = mismatches == 0
        print(f"    derived-cell equality: {mismatches} mismatches -> " + ("PASS" if ok else "FAIL"))
        if ok:
            residue = await conn.fetchval(
                "SELECT count(*) FROM super_admin.usage_events u "
                "WHERE u.metric = 'bookings' AND NOT EXISTS ("
                "  SELECT 1 FROM public.bookings b "
                "  WHERE b.org_id = u.org_id "
                "  AND date_trunc('month', b.created_at)::date = u.event_month)"
            )
            print(f"    note: {residue} bookings cell(s) with no source bookings (test residue, left untouched)")
    finally:
        await conn.close()
    print(f"\nBackfill complete: {total_rows} usage_events upserted.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))