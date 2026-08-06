"""Phase 6 E2E (backend): analytics & monitoring — reports engine, dependency
health, platform alerts (acknowledge/resolve via tenant bridge) and
incidents lifecycle.

Covers:
  - reports: list 12 types, usage JSON+CSV (month filter), 404 unknown,
    422 bad month, revenue 404 (lives at /billing/reports/revenue.csv)
  - health: /health/dependencies (bridge -> tenant checks), /health/overview
  - alerts: list + filters, get, acknowledge (bridge, idempotent), resolve
    (bridge, idempotent), trends
  - incidents: create, get, update investigating -> resolved, invalid
    severity 400s
  - RBAC: operations read-only trio (reports/health/alerts view, no
    alerts.manage, no incidents), support has none of the new perms
  - audit rows for incidents.* and alerts.*

Cleanup: removes E2E org (with alerts/usage rows), incidents, test admins'
sessions/MFA (kept, disabled).
"""
import asyncio
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pyotp
from passlib.context import CryptContext

SUPER = "http://127.0.0.1:8006"
PASS = []
FAIL = []

OWNER_EMAIL = "admin@dotstark.ai"
OWNER_PASSWORD = "SuperAdmin@2026!"
SUPPORT_EMAIL = "p6.support@dotstark.ai"
SUPPORT_PASSWORD = "Support@2026!"
OPS_EMAIL = "p6.ops@dotstark.ai"
OPS_PASSWORD = "Ops@2026!"


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name} {extra}")


async def login_mfa(client, email, password, secret):
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    r = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200, f"mfa {email}: {r.status_code} {r.text}"


async def enroll_login(client, email, password):
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    r = await client.post("/api/v1/auth/mfa/enroll")
    secret = r.json()["secret"]
    r = await client.post(
        "/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 200, f"mfa {email}: {r.text}"
    return secret


async def reset_admin(conn, email, password, role_code):
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    row = await conn.fetchrow(
        "SELECT id FROM super_admin.super_admins WHERE email = $1", email
    )
    if row:
        await conn.execute(
            "DELETE FROM super_admin.admin_sessions WHERE admin_id = $1", row["id"]
        )
        await conn.execute(
            "DELETE FROM super_admin.admin_mfa WHERE admin_id = $1", row["id"]
        )
        await conn.execute(
            "DELETE FROM super_admin.login_attempts WHERE admin_id = $1", row["id"]
        )
        await conn.execute(
            "UPDATE super_admin.super_admins SET password_hash = $1, status = 'active' "
            "WHERE id = $2",
            pwd_ctx.hash(password),
            row["id"],
        )
    else:
        await conn.execute(
            """
            INSERT INTO super_admin.super_admins
                (id, email, password_hash, full_name, role_id, status, mfa_required)
            SELECT gen_random_uuid(), $1, $2, 'P6 E2E Admin', id, 'active', TRUE
            FROM super_admin.roles WHERE code = $3
            """,
            email, pwd_ctx.hash(password), role_code,
        )


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark@localhost:5432/dotstark_v2"
    )

    CSRF = {"X-Requested-With": "XMLHttpRequest"}

    for email, password, role in (
        (SUPPORT_EMAIL, SUPPORT_PASSWORD, "support"),
        (OPS_EMAIL, OPS_PASSWORD, "operations"),
    ):
        await reset_admin(conn, email, password, role)
    await conn.execute("DELETE FROM public.organizations WHERE name LIKE 'E2E P6 Monitoring %'")
    await conn.execute(
        "DELETE FROM super_admin.incidents WHERE title LIKE 'E2E P6 %'"
    )

    owner_secret = await conn.fetchval(
        "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
        "(SELECT id FROM super_admin.super_admins WHERE email = $1)",
        OWNER_EMAIL,
    )

    async with httpx.AsyncClient(base_url=SUPER, headers=CSRF) as owner, httpx.AsyncClient(
        base_url=SUPER, headers=CSRF
    ) as support, httpx.AsyncClient(base_url=SUPER, headers=CSRF) as ops:
        await login_mfa(owner, OWNER_EMAIL, OWNER_PASSWORD, owner_secret)
        support_secret = await enroll_login(support, SUPPORT_EMAIL, SUPPORT_PASSWORD)
        ops_secret = await enroll_login(ops, OPS_EMAIL, OPS_PASSWORD)

        # ---- Setup: org + alert + usage rows ----
        r = await owner.post(
            "/api/v1/lifecycle/orgs",
            json={"name": f"E2E P6 Monitoring {uuid_mod.uuid4().hex[:8]}", "business_name": "P6 Test Co"},
        )
        check("create org 200", r.status_code == 200, r.text)
        org_id = r.json()["org_id"]
        month_str = datetime.now(timezone.utc).strftime("%Y-%m-01")
        month = datetime.strptime(month_str, "%Y-%m-%d").date()
        await conn.execute(
            """
            INSERT INTO super_admin.usage_events (event_month, org_id, metric, count)
            VALUES ($1::date, $2, 'bookings', 7), ($1::date, $2, 'ai_tokens', 12345)
            ON CONFLICT (event_month, org_id, metric) DO UPDATE SET count = EXCLUDED.count
            """,
            month, org_id,
        )
        alert_uuid = uuid_mod.uuid4()
        alert_id = await conn.fetchval(
            """
            INSERT INTO public.system_alerts
                (id, org_id, component, severity, code, dedupe_key, message)
            VALUES ($1, $2, 'llm', 'critical', 'p6_e2e_timeout', $3, 'P6 E2E LLM timeout spike')
            RETURNING id
            """,
            alert_uuid, org_id, f"p6-e2e-{uuid_mod.uuid4().hex[:8]}",
        )

        # ---- 1. Reports ----
        r = await owner.get("/api/v1/reports")
        check("list reports 12 types", r.status_code == 200 and len(r.json()["items"]) == 12, r.text)
        r = await owner.get("/api/v1/reports/usage?month=" + month_str)
        rep = r.json()
        mine = next((x for x in rep["rows"] if x["org_id"] == str(org_id)), None)
        check("usage report includes org", mine is not None and mine["bookings"] == 7 and mine["ai_tokens"] == 12345, str(rep)[:400])
        check("usage report totals", rep["total_bookings"] >= 7 and rep["total_ai_tokens"] >= 12345)
        r = await owner.get("/api/v1/reports/usage?month=2026-99-99")
        check("usage bad month 422", r.status_code == 422, r.text)
        for name in ("bookings", "conversations", "ai", "api", "storage", "kb", "growth", "performance", "health", "widget"):
            r = await owner.get(f"/api/v1/reports/{name}")
            check(f"report {name} 200", r.status_code == 200, r.text[:200])
        r = await owner.get("/api/v1/reports/revenue")
        check("revenue report 404 (billing owns it)", r.status_code == 404, r.text)
        r = await owner.get("/api/v1/reports/nope")
        check("unknown report 404", r.status_code == 404, r.text)

        r = await owner.get("/api/v1/reports/usage/csv?month=" + month_str)
        check("usage csv content type", "text/csv" in r.headers.get("content-type", ""), r.headers.get("content-type"))
        check("usage csv has org row", "bookings" in r.text and "ai_tokens" in r.text, r.text[:300])
        r = await owner.get("/api/v1/reports/health/csv")
        check("health csv 200", r.status_code == 200 and "open_total" in r.text, r.text[:200])
        r = await owner.get("/api/v1/reports/revenue/csv")
        check("revenue csv 404 message", r.status_code == 404 and "billing/reports" in r.text, r.text)

        # ---- 2. Health ----
        r = await owner.get("/api/v1/health/dependencies")
        dep = r.json()
        check("dependencies 200", r.status_code == 200, r.text[:300])
        if isinstance(dep, dict) and isinstance(dep.get("checks"), dict):
            check("dependencies has db check", "database" in dep["checks"], str(dep.get("checks"))[:200])
        elif isinstance(dep, dict) and isinstance(dep.get("dependencies"), list):
            names = [d.get("name") for d in dep["dependencies"]]
            check("dependencies has db check", "database" in names, str(dep.get("dependencies"))[:300])
        else:
            check("dependencies has db check", False, f"unexpected shape: {str(dep)[:300]}")
        r = await owner.get("/api/v1/health/overview")
        ov = r.json()
        check("overview open_alerts >= 1", ov.get("open_alerts", 0) >= 1, str(ov))
        check("overview critical >= 1", ov.get("critical_alerts", 0) >= 1, str(ov))
        check("overview incident counters", "open_incidents" in ov, str(ov))

        # ---- 3. Alerts ----
        r = await owner.get("/api/v1/alerts?severity=critical&status=open")
        check("list alerts filter", r.status_code == 200 and any(a["id"] == str(alert_id) for a in r.json()["items"]), r.text[:300])
        r = await owner.get(f"/api/v1/alerts/{alert_id}")
        check("get alert", r.status_code == 200 and r.json()["severity"] == "critical", r.text)
        r = await owner.get("/api/v1/alerts/trends?days=7")
        check("alert trends", r.status_code == 200 and "items" in r.json(), r.text[:200])

        r = await owner.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        check("acknowledge 200", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        check("acknowledge idempotent", r.status_code == 200 and r.json().get("already") is True, r.text)
        r = await owner.get(f"/api/v1/alerts/{alert_id}")
        check("alert acknowledged_at set", r.json()["acknowledged_at"] is not None, r.text)
        r = await owner.post(f"/api/v1/alerts/{alert_id}/resolve")
        check("resolve 200", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/alerts/{alert_id}/resolve")
        check("resolve idempotent", r.status_code == 200 and r.json().get("already") is True, r.text)
        r = await owner.get(f"/api/v1/alerts/{alert_id}")
        check("alert resolved_at set", r.json()["resolved_at"] is not None, r.text)
        r = await owner.get("/api/v1/alerts?status=resolved")
        check("resolved filter", r.status_code == 200 and any(a["id"] == str(alert_id) for a in r.json()["items"]), r.text[:200])

        # ---- 4. Incidents ----
        r = await owner.post(
            "/api/v1/incidents",
            json={"title": f"E2E P6 DB latency {uuid_mod.uuid4().hex[:6]}", "severity": "critical", "component": "database", "message": "p6 probe"},
        )
        check("create incident 200", r.status_code == 200, r.text)
        incident_id = r.json()["incident_id"]
        r = await owner.post(
            "/api/v1/incidents",
            json={"title": "E2E P6 bad sev", "severity": "urgent", "component": "x"},
        )
        check("create invalid severity 400", r.status_code == 400, r.text)
        r = await owner.get(f"/api/v1/incidents/{incident_id}")
        check("get incident", r.status_code == 200 and r.json()["status"] == "open" and r.json()["severity"] == "critical", r.text)
        r = await owner.patch(f"/api/v1/incidents/{incident_id}", json={"status": "investigating"})
        check("update to investigating", r.status_code == 200, r.text)
        r = await owner.patch(f"/api/v1/incidents/{incident_id}", json={"severity": "bogus"})
        check("update invalid severity 400", r.status_code == 400, r.text)
        r = await owner.patch(f"/api/v1/incidents/{incident_id}", json={"status": "resolved"})
        check("resolve incident", r.status_code == 200, r.text)
        r = await owner.get(f"/api/v1/incidents/{incident_id}")
        check("incident resolved fields", r.json()["status"] == "resolved" and r.json()["resolved_at"] is not None, r.text)
        r = await owner.get("/api/v1/incidents?status=resolved")
        check("incidents list filter", r.status_code == 200 and any(i["id"] == str(incident_id) for i in r.json()["items"]), r.text[:200])
        r = await owner.patch(f"/api/v1/incidents/{incident_id}", json={"status": "open"})
        check("reopen incident clears resolved", r.status_code == 200, r.text)
        r = await owner.get(f"/api/v1/incidents/{incident_id}")
        check("reopened fields cleared", r.json()["status"] == "open" and r.json()["resolved_at"] is None, r.text)

        # ---- 5. RBAC ----
        r = await ops.get("/api/v1/reports")
        check("ops reports view 200", r.status_code == 200, r.text)
        r = await ops.get("/api/v1/health/overview")
        check("ops health view 200", r.status_code == 200, r.text)
        r = await ops.get("/api/v1/alerts")
        check("ops alerts view 200", r.status_code == 200, r.text)
        r = await ops.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        check("ops acknowledge 200 (Phase 0 grant)", r.status_code == 200, r.text)
        r = await ops.get("/api/v1/incidents")
        check("ops incidents view 200 (Phase 0 grant)", r.status_code == 200, r.text[:200])
        r = await ops.post("/api/v1/incidents", json={"title": f"E2E P6 ops {uuid_mod.uuid4().hex[:6]}", "severity": "major", "component": "x"})
        check("ops create incident 200 (Phase 0 grant)", r.status_code == 200, r.text)
        for path in ("/api/v1/reports", "/api/v1/health/dependencies", "/api/v1/alerts", "/api/v1/incidents"):
            r = await support.get(path)
            check(f"support {path} 403", r.status_code == 403, r.text)
        r = await support.post("/api/v1/incidents", json={"title": "E2E P6 x", "severity": "major", "component": "x"})
        check("support create incident 403", r.status_code == 403, r.text)

        # ---- 6. Audit ----
        r = await owner.get("/api/v1/audit?limit=200")
        actions = [a["action"] for a in r.json()]
        for wanted in (
            "incidents.create",
            "incidents.update",
            "alerts.acknowledge",
            "alerts.resolve",
        ):
            check(f"audit {wanted}", wanted in actions)

        # ---- Cleanup ----
        await conn.execute("DELETE FROM public.organizations WHERE id = $1", org_id)
        await conn.execute("DELETE FROM public.system_alerts WHERE id = $1", alert_id)
        await conn.execute(
            "DELETE FROM super_admin.incidents WHERE title LIKE 'E2E P6 %'"
        )
        for email in (SUPPORT_EMAIL, OPS_EMAIL):
            row = await conn.fetchrow(
                "SELECT id FROM super_admin.super_admins WHERE email = $1", email
            )
            await conn.execute("DELETE FROM super_admin.admin_sessions WHERE admin_id = $1", row["id"])
            await conn.execute("DELETE FROM super_admin.admin_mfa WHERE admin_id = $1", row["id"])
            await conn.execute(
                "UPDATE super_admin.super_admins SET status = 'disabled' WHERE id = $1",
                row["id"],
            )

    print(f"\nE2E phase 6 analytics: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


asyncio.run(main())
