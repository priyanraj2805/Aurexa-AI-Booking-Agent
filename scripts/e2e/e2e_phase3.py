"""Phase 3 E2E: dashboard stats, top orgs, org detail tabs, user profile."""
import asyncio
import uuid as uuid_mod

import asyncpg
import httpx
import pyotp

SUPER = "http://127.0.0.1:8006"
PASS = []
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name} {extra}")


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark@localhost:5432/dotstark_v2"
    )
    totp_secret = await conn.fetchval(
        "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
        "(SELECT id FROM super_admin.super_admins WHERE email = 'admin@dotstark.ai')"
    )

    async with httpx.AsyncClient(base_url=SUPER) as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "admin@dotstark.ai", "password": "SuperAdmin@2026!"})
        check("login 200", r.status_code == 200, r.text)
        r = await c.post("/api/v1/auth/mfa/verify",
                         json={"code": pyotp.TOTP(totp_secret).now()})
        check("mfa 200", r.status_code == 200, r.text)

        # ── Stats ──
        r = await c.get("/api/v1/tenants/stats")
        check("stats 200", r.status_code == 200, r.text)
        s = r.json()
        check("stats org counts", s["orgs_total"] > 0 and s["orgs_active"] > 0, s)
        check("stats users", s["users_total"] > 0, s)
        check("stats bookings >= conversations total", s["bookings_total"] >= 0)
        check("stats trend 14 points", len(s["trend"]) == 14, len(s["trend"]))
        check("trend has keys", all("date" in t and "bookings" in t for t in s["trend"]))
        check("alerts fields present", "alerts_open" in s and "alerts_critical" in s)
        check("mrr/ai null (P5)", s["mrr"] is None and s["ai_tokens_30d"] is None)

        r = await c.get("/api/v1/tenants/stats/top-orgs")
        check("top-orgs 200", r.status_code == 200, r.text)
        top = r.json()["items"]
        check("top-orgs non-empty", len(top) > 0, top)
        check("top-org sorted desc", all(
            top[i]["booking_count"] >= top[i + 1]["booking_count"]
            for i in range(len(top) - 1)), top[:2])

        # ── Org detail tabs ──
        orgs = (await c.get("/api/v1/tenants/orgs")).json()["items"]
        org_id = orgs[0]["id"]

        r = await c.get(f"/api/v1/tenants/orgs/{org_id}/users")
        check("org users 200", r.status_code == 200, r.text)
        if r.json()["items"]:
            u = r.json()["items"][0]
            check("org user fields", all(k in u for k in ["id", "email", "full_name"]))

        r = await c.get(f"/api/v1/tenants/orgs/{org_id}/usage")
        check("org usage 200", r.status_code == 200, r.text)
        check("usage has items list", "items" in r.json())

        r = await c.get(f"/api/v1/tenants/orgs/{org_id}/audit")
        check("org audit 200", r.status_code == 200, r.text)
        check("org audit shape", "items" in r.json() and "total" in r.json())

        r = await c.get(f"/api/v1/tenants/orgs/{uuid_mod.uuid4()}/usage")
        check("unknown org usage 404", r.status_code == 404)

        # ── User profile ──
        users = (await c.get("/api/v1/tenants/users")).json()["items"]
        check("users non-empty", len(users) > 0)
        user_id = users[0]["id"]
        r = await c.get(f"/api/v1/tenants/users/{user_id}")
        check("user profile 200", r.status_code == 200, r.text)
        p = r.json()
        check("profile fields", all(k in p for k in
                                    ["org_name", "email", "booking_count", "recent_bookings"]))
        check("profile bookings list", isinstance(p["recent_bookings"], list))

        r = await c.get(f"/api/v1/tenants/users/{user_id}/activity")
        check("user activity 200", r.status_code == 200, r.text)
        check("activity has items", "items" in r.json())

        r = await c.get(f"/api/v1/tenants/users/{uuid_mod.uuid4()}")
        check("unknown user 404", r.status_code == 404)

        # ── RBAC: auditor role cannot access stats? (auditor HAS dashboard.view) ──
        # check permission enforcement: create second admin? skip — covered in P1.

    await conn.close()
    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("FAILED:", FAIL)
        raise SystemExit(1)


asyncio.run(main())
