"""Phase 2 E2E: read APIs (backend-super) + internal bridge (main backend)."""
import asyncio
import hashlib
import hmac
import time
import uuid as uuid_mod

import asyncpg
import httpx
import pyotp

MAIN_LIVE = "http://127.0.0.1:8005"
MAIN_TEST = "http://127.0.0.1:8005"
SUPER = "http://127.0.0.1:8006"
BRIDGE_SECRET = "dev_bridge_secret_2026"

PASS = []
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name} {extra}")


def sign(method, path, admin_email, body=b"", ts=None, secret=BRIDGE_SECRET):
    ts = ts or int(time.time())
    digest = hashlib.sha256(body).hexdigest()
    canonical = ".".join([str(ts), method, path, admin_email, digest])
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return ts, sig


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark@localhost:5432/dotstark_v2"
    )

    # ── A. Bridge surface on the LIVE backend ───────────────────────────
    print("A. Live backend (bridge surface):")
    async with httpx.AsyncClient(base_url=MAIN_LIVE, follow_redirects=False) as c:
        bogus = str(uuid_mod.uuid4())
        ts, sig = sign("POST", f"/api/v1/internal/bridge/orgs/{bogus}/suspend", "a@b.c")
        r = await c.post(
            f"/api/v1/internal/bridge/orgs/{bogus}/suspend",
            headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                     "X-Bridge-Admin-Email": "a@b.c"},
        )
        # Pre-cutover (INTERNAL_BRIDGE_ENABLED=false): bridge surface 404s.
        # Post-cutover (enabled): bridge is up but every unsigned/wrong-signature
        # call is rejected 401 — the surface must never answer unauthenticated.
        check("live bridge off (404) or guarded (401/403)",
              r.status_code == 404 or r.status_code in (401, 403),
              f"{r.status_code} {r.text[:80]}")

    # ── B. Bridge auth rejection paths (test instance) ───────────────────
    print("B. Bridge auth rejection:")
    async with httpx.AsyncClient(base_url=MAIN_TEST) as c:
        r = await c.post("/api/v1/internal/bridge/orgs/x/suspend")
        check("missing headers 401", r.status_code == 401, r.text)

        ts, sig = sign("POST", "/api/v1/internal/bridge/orgs/x/suspend", "a@b.c",
                       ts=int(time.time()) - 10000)
        r = await c.post(
            "/api/v1/internal/bridge/orgs/x/suspend",
            headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                     "X-Bridge-Admin-Email": "a@b.c"},
        )
        check("stale timestamp 401", r.status_code == 401, r.text)

        ts, sig = sign("POST", "/api/v1/internal/bridge/orgs/x/suspend", "a@b.c",
                       secret="wrong_secret")
        r = await c.post(
            "/api/v1/internal/bridge/orgs/x/suspend",
            headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                     "X-Bridge-Admin-Email": "a@b.c"},
        )
        check("wrong secret 401", r.status_code == 401, r.text)

        bogus_id = str(uuid_mod.uuid4())
        ts, sig = sign("POST", f"/api/v1/internal/bridge/orgs/{bogus_id}/suspend", "a@b.c")
        r = await c.post(
            f"/api/v1/internal/bridge/orgs/{bogus_id}/suspend",
            headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                     "X-Bridge-Admin-Email": "a@b.c"},
        )
        check("unknown org 404", r.status_code == 404, r.text)
    # ── C. Bridge happy path lifecycle ───────────────────────────────────
    print("C. Bridge lifecycle:")
    admin_email = "admin@dotstark.ai"
    async with httpx.AsyncClient(base_url=MAIN_TEST) as c:
        # create org
        body = b'{"name": "E2E Bridge Test Org", "business_name": "E2E Corp"}'
        ts, sig = sign("POST", "/api/v1/internal/bridge/orgs", admin_email, body)
        r = await c.post("/api/v1/internal/bridge/orgs", content=body,
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email,
                                  "Content-Type": "application/json"})
        check("create org 200", r.status_code == 200, r.text)
        org_id = r.json()["org_id"]
        api_key = r.json()["api_key"]
        check("create returns api key", api_key.startswith("ds_"), api_key)

        # telemetry: org.create counter
        cnt = await conn.fetchval(
            "SELECT count FROM super_admin.usage_events "
            "WHERE org_id = $1 AND metric = 'org.create'", uuid_mod.UUID(org_id))
        check("telemetry org.create=1", cnt == 1, cnt)

        # org exists in DB
        row = await conn.fetchrow(
            "SELECT is_active FROM organizations WHERE id = $1", uuid_mod.UUID(org_id))
        check("org row created active", row and row["is_active"] is True)

        # suspend
        ts, sig = sign("POST", f"/api/v1/internal/bridge/orgs/{org_id}/suspend", admin_email)
        r = await c.post(f"/api/v1/internal/bridge/orgs/{org_id}/suspend",
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email})
        check("suspend 200", r.status_code == 200, r.text)
        row = await conn.fetchrow(
            "SELECT is_active FROM organizations WHERE id = $1", uuid_mod.UUID(org_id))
        check("org suspended in DB", row["is_active"] is False)

        # idempotent suspend
        ts, sig = sign("POST", f"/api/v1/internal/bridge/orgs/{org_id}/suspend", admin_email)
        r = await c.post(f"/api/v1/internal/bridge/orgs/{org_id}/suspend",
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email})
        check("suspend idempotent", r.status_code == 200 and r.json().get("already") is True)

        # restore
        ts, sig = sign("POST", f"/api/v1/internal/bridge/orgs/{org_id}/restore", admin_email)
        r = await c.post(f"/api/v1/internal/bridge/orgs/{org_id}/restore",
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email})
        check("restore 200", r.status_code == 200, r.text)
        row = await conn.fetchrow(
            "SELECT is_active FROM organizations WHERE id = $1", uuid_mod.UUID(org_id))
        check("org restored", row["is_active"] is True)

        # config update
        body = b'{"require_otp": false, "session_retention_days": 7}'
        ts, sig = sign("PATCH", f"/api/v1/internal/bridge/orgs/{org_id}/config", admin_email, body)
        r = await c.patch(f"/api/v1/internal/bridge/orgs/{org_id}/config", content=body,
                          headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                   "X-Bridge-Admin-Email": admin_email,
                                   "Content-Type": "application/json"})
        check("config update 200", r.status_code == 200, r.text)
        row = await conn.fetchrow(
            "SELECT require_otp, session_retention_days FROM org_configs WHERE org_id = $1",
            uuid_mod.UUID(org_id))
        check("config applied", row["require_otp"] is False and row["session_retention_days"] == 7,
              row)

        # user disable/enable: create a user directly, then bridge them
        user_id = str(uuid_mod.uuid4())
        await conn.execute(
            "INSERT INTO users (id, email, hashed_password, full_name, org_id, is_active) "
            "VALUES ($1, $2, 'x', 'E2E User', $3, true)",
            user_id, f"e2euser-{user_id[:8]}@test.dev", uuid_mod.UUID(org_id))
        ts, sig = sign("POST", f"/api/v1/internal/bridge/users/{user_id}/disable", admin_email)
        r = await c.post(f"/api/v1/internal/bridge/users/{user_id}/disable",
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email})
        check("disable user 200", r.status_code == 200, r.text)
        row = await conn.fetchrow("SELECT is_active FROM users WHERE id = $1", user_id)
        check("user disabled", row["is_active"] is False)
        ts, sig = sign("POST", f"/api/v1/internal/bridge/users/{user_id}/enable", admin_email)
        r = await c.post(f"/api/v1/internal/bridge/users/{user_id}/enable",
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email})
        check("enable user 200", r.status_code == 200, r.text)
        row = await conn.fetchrow("SELECT is_active FROM users WHERE id = $1", user_id)
        check("user enabled", row["is_active"] is True)

        # tenant-side audit entries
        actions = await conn.fetch(
            "SELECT action, actor_email FROM audit_logs "
            "WHERE org_id = $1 AND actor_email = $2 ORDER BY created_at",
            uuid_mod.UUID(org_id), admin_email)
        got = [a["action"] for a in actions]
        for expected in ["org.create", "org.suspend", "org.restore", "config.update",
                         "user.disable", "user.enable"]:
            check(f"audit {expected}", expected in got, got)

        # telemetry counters
        cnt = await conn.fetchval(
            "SELECT count FROM super_admin.usage_events WHERE org_id = $1 AND metric = 'org.suspend'",
            uuid_mod.UUID(org_id))
        check("telemetry org.suspend=1", cnt == 1, cnt)

        # delete org (cascade)
        ts, sig = sign("POST", f"/api/v1/internal/bridge/orgs/{org_id}/delete", admin_email)
        r = await c.post(f"/api/v1/internal/bridge/orgs/{org_id}/delete",
                         headers={"X-Bridge-Timestamp": str(ts), "X-Bridge-Signature": sig,
                                  "X-Bridge-Admin-Email": admin_email})
        check("delete org 200", r.status_code == 200, r.text)
        row = await conn.fetchrow(
            "SELECT id FROM organizations WHERE id = $1", uuid_mod.UUID(org_id))
        check("org deleted (cascade)", row is None)
        users = await conn.fetchval("SELECT count(*) FROM users WHERE org_id = $1",
                                    uuid_mod.UUID(org_id))
        check("users cascaded", users == 0)

    # ── D. backend-super read APIs ───────────────────────────────────────
    print("D. backend-super read APIs:")
    totp_secret = await conn.fetchval(
        "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
        "(SELECT id FROM super_admin.super_admins WHERE email = 'admin@dotstark.ai')")
    check("totp secret found", totp_secret is not None)

    async with httpx.AsyncClient(base_url=SUPER, follow_redirects=True) as c:
        r = await c.post("/api/v1/auth/login", json={"email": "admin@dotstark.ai",
                                                     "password": "SuperAdmin@2026!"})
        check("login 200", r.status_code == 200, r.text)

        r = await c.post("/api/v1/auth/mfa/verify",
                         json={"code": pyotp.TOTP(totp_secret).now()})
        check("mfa verify 200", r.status_code == 200, r.text)

        r = await c.get("/api/v1/tenants/orgs")
        check("GET /tenants/orgs 200", r.status_code == 200, r.text)
        data = r.json()
        check("orgs list has items", data["total"] > 0, data["total"])
        org = data["items"][0]
        check("org fields", all(k in org for k in
                                ["id", "name", "api_key_masked", "is_active",
                                 "user_count", "booking_count"]), org.keys())
        check("api key masked", "…" in org["api_key_masked"] or "ds_" in org["api_key_masked"])

        r = await c.get("/api/v1/tenants/orgs", params={"search": "e2e"})
        check("org search works", r.status_code == 200)

        r = await c.get(f"/api/v1/tenants/orgs/{org['id']}")
        check("org detail 200", r.status_code == 200, r.text)
        detail = r.json()
        check("detail has config", "config" in detail and "timezone" in detail["config"])

        r = await c.get(f"/api/v1/tenants/orgs/{org['id']}/bookings")
        check("org bookings 200", r.status_code == 200, r.text)

        r = await c.get(f"/api/v1/tenants/orgs/{org['id']}/sessions")
        check("org sessions 200", r.status_code == 200, r.text)

        r = await c.get("/api/v1/tenants/users")
        check("users search 200", r.status_code == 200, r.text)
        if r.json()["items"]:
            u = r.json()["items"][0]
            check("user fields", all(k in u for k in
                                     ["id", "org_id", "org_name", "email", "full_name",
                                      "is_active"]), u.keys())

        r = await c.get(f"/api/v1/tenants/orgs/{uuid_mod.uuid4()}")
        check("unknown org 404", r.status_code == 404)

        r = await c.get("/api/v1/tenants/users", params={"search": "zzzz-not-found-zzz"})
        check("user search empty result", r.json()["total"] == 0)

        r = await c.post("/api/v1/tenants/orgs", json={})
        check("no write surface on read pool 403/404/405",
              r.status_code in (404, 405, 403, 401), r.status_code)

    await conn.close()

    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("FAILED:", FAIL)
        raise SystemExit(1)


asyncio.run(main())
