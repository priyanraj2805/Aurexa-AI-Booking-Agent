"""Phase 5 E2E (backend part 1): plans CRUD + usage limits + assignments.

Covers:
  - GET /plans seeded catalog (starter/pro/enterprise, null = unlimited)
  - POST/PATCH/DELETE plans: validation 422s, duplicate code 409,
    hard delete vs archive (when assigned)
  - assignment flow: assign -> change plan -> one ACTIVE row; 404/422s
  - usage read-out: usage_events month metrics + org-plan with usage
  - RBAC: support role (no plans perms) 403s; operations role positive path
  - audit rows for plan.create/update/archive/assign

Cleanup: removes the E2E org (CASCADE wipes assignments), deletes leftover
test plans, and disables + wipes MFA/sessions of the test admins (actors are
kept forever because the audit log is append-only).
"""
import asyncio
import hashlib
import json
import uuid as uuid_mod
from datetime import datetime, timezone

import asyncpg
import httpx
import pyotp
from passlib.context import CryptContext

SUPER = "http://127.0.0.1:8006"
PASS = []
FAIL = []

OWNER_EMAIL = "admin@dotstark.ai"
OWNER_PASSWORD = "SuperAdmin@2026!"
SUPPORT_EMAIL = "p4.support@dotstark.ai"
SUPPORT_PASSWORD = "Support@2026!"
OPS_EMAIL = "p5.ops@dotstark.ai"
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


async def mint_admin(conn, email, password, role_code):
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    admin_id = await conn.fetchval(
        """
        INSERT INTO super_admin.super_admins
            (id, email, password_hash, full_name, role_id, status, mfa_required)
        SELECT gen_random_uuid(), $1, $2, 'P5 E2E Admin', id, 'active', TRUE
        FROM super_admin.roles WHERE code = $3
        RETURNING id
        """,
        email, pwd_ctx.hash(password), role_code,
    )
    return admin_id


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
        await mint_admin(conn, email, password, role_code)


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark@localhost:5432/dotstark_v2"
    )

    CSRF = {"X-Requested-With": "XMLHttpRequest"}
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    await reset_admin(conn, SUPPORT_EMAIL, SUPPORT_PASSWORD, "support")
    await reset_admin(conn, OPS_EMAIL, OPS_PASSWORD, "operations")

    await conn.execute(
        "DELETE FROM super_admin.plan_assignments "
        "WHERE plan_id IN (SELECT id FROM super_admin.plans WHERE code LIKE 'p5-%')"
    )
    await conn.execute("DELETE FROM super_admin.plans WHERE code LIKE 'p5-%'")
    await conn.execute(
        "DELETE FROM public.organizations WHERE name LIKE 'E2E P5 Plans %'"
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
        r = await support.post("/api/v1/auth/login", json={"email": SUPPORT_EMAIL, "password": SUPPORT_PASSWORD})
        assert r.status_code == 200, f"support login: {r.text}"
        r = await support.post("/api/v1/auth/mfa/enroll")
        support_secret = r.json()["secret"]
        r = await support.post("/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(support_secret).now()})
        assert r.status_code == 200, f"support mfa: {r.text}"
        r = await ops.post("/api/v1/auth/login", json={"email": OPS_EMAIL, "password": OPS_PASSWORD})
        assert r.status_code == 200, r.text
        r = await ops.post("/api/v1/auth/mfa/enroll")
        ops_secret = r.json()["secret"]
        r = await ops.post("/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(ops_secret).now()})
        assert r.status_code == 200, f"ops mfa: {r.text}"

        # ══ 1. Seeded catalog ══
        r = await owner.get("/api/v1/plans")
        check("plans list 200", r.status_code == 200, r.text)
        items = r.json()["items"]
        codes = [p["code"] for p in items]
        check("3 seeded plans", {"starter", "pro", "enterprise"} <= set(codes), codes)
        ent = next(p for p in items if p["code"] == "enterprise")
        check("enterprise unlimited bookings (null)", ent["limits"].get("bookings_per_month") is None)
        check("enterprise price 49000", ent["monthly_price_cents"] == 49000)
        starter = next(p for p in items if p["code"] == "starter")
        check("starter limits present", starter["limits"].get("bookings_per_month") == 100)

        # ══ 2. Detail ══
        r = await owner.get(f"/api/v1/plans/{starter['id']}")
        check("plan detail 200", r.status_code == 200 and r.json()["code"] == "starter", r.text)

        # ══ 3. Create ══
        r = await owner.post(
            "/api/v1/plans",
            json={
                "code": "p5-test-plan",
                "name": "P5 Test Plan",
                "description": "E2E",
                "monthly_price_cents": 2500,
                "period": "annual",
                "limits": {"bookings_per_month": 500, "ai_tokens_per_month": 250_000, "seats": 10, "storage_gb": 5},
            },
        )
        check("create plan 200", r.status_code == 200, r.text)
        test_plan_id = r.json()["plan_id"]
        check("create returns id", bool(test_plan_id))

        r = await owner.post(
            "/api/v1/plans",
            json={"code": "p5-test-plan", "name": "Dup", "limits": {}},
        )
        check("duplicate code 409", r.status_code == 409, r.text)

        bad_cases = [
            ({"code": "Bad Code!", "name": "x", "limits": {}}, "bad code"),
            ({"code": "ok-code", "name": "x", "period": "weekly", "limits": {}}, "bad period"),
            ({"code": "ok-code2", "name": "x", "monthly_price_cents": -1, "limits": {}}, "negative price"),
            ({"code": "ok-code3", "name": "x", "limits": {"unknown_metric": 5}}, "unknown limit key"),
            ({"code": "ok-code4", "name": "x", "limits": {"seats": -3}}, "negative limit"),
        ]
        for body, label in bad_cases:
            r = await owner.post("/api/v1/plans", json=body)
            check(f"create {label} 422", r.status_code == 422, r.text)

        # ══ 4. Update ══
        r = await owner.patch(
            f"/api/v1/plans/{test_plan_id}",
            json={"name": "P5 Test Plan v2", "limits": {"bookings_per_month": 600}},
        )
        check("update plan 200", r.status_code == 200, r.text)
        r = await owner.get(f"/api/v1/plans/{test_plan_id}")
        check("update applied", r.json()["name"] == "P5 Test Plan v2" and r.json()["limits"]["bookings_per_month"] == 600, r.text)

        # ══ 5. Assignment flow ══
        r = await owner.post(
            "/api/v1/lifecycle/orgs",
            json={"name": f"E2E P5 Plans {uuid_mod.uuid4().hex[:8]}", "business_name": "P5 Test Co"},
        )
        check("create org 200", r.status_code == 200, r.text)
        org_id = r.json()["org_id"]

        r = await owner.post(f"/api/v1/plans/{starter['id']}/assign", json={"org_id": org_id, "note": "onboarding"})
        check("assign starter 200", r.status_code == 200 and r.json()["plan_code"] == "starter", r.text)

        r = await owner.get(f"/api/v1/plans/orgs/{org_id}/plan")
        check("org plan reflects assignment", r.status_code == 200 and r.json()["plan"]["code"] == "starter", r.text)
        # Post-cutover: creating the org wrote an org.create telemetry counter,
        # so usage is no longer empty — it proves telemetry is live.
        check("org plan usage has org.create telemetry", r.json().get("usage", {}).get("org.create") == 1, r.json().get("usage"))

        r = await owner.post(f"/api/v1/plans/{ent['id']}/assign", json={"org_id": org_id})
        check("reassign to enterprise 200", r.status_code == 200, r.text)
        active = await conn.fetchval(
            "SELECT count(*) FROM super_admin.plan_assignments WHERE org_id = $1 AND status = 'active'",
            org_id,
        )
        check("one active assignment", active == 1, str(active))
        r = await owner.get(f"/api/v1/plans/orgs/{org_id}/plan")
        check("org plan now enterprise", r.json()["plan"]["code"] == "enterprise", r.text)

        r = await owner.post(f"/api/v1/plans/{starter['id']}/assign", json={"org_id": "00000000-0000-0000-0000-000000000000"})
        check("assign missing org 404", r.status_code == 404, r.text)

        # ══ 6. Usage read-out ══
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        await conn.execute(
            """
            INSERT INTO super_admin.usage_events (event_month, org_id, metric, count, updated_at)
            VALUES (date_trunc('month', now())::date, $1, 'bookings', 5, now()),
                   (date_trunc('month', now())::date, $1, 'ai_tokens', 1234, now())
            ON CONFLICT (event_month, org_id, metric)
            DO UPDATE SET count = EXCLUDED.count, updated_at = now()
            """,
            org_id,
        )
        r = await owner.get(f"/api/v1/plans/orgs/{org_id}/usage?month={month}")
        body = r.json()
        check("org usage 200", r.status_code == 200 and body["metrics"]["bookings"] == 5 and body["metrics"]["ai_tokens"] == 1234, r.text)
        r = await owner.get(f"/api/v1/plans/orgs/{org_id}/plan")
        check("org plan includes usage", r.json()["usage"].get("bookings") == 5, r.text)
        r = await owner.get(f"/api/v1/plans/orgs/{org_id}/usage?month=not-a-month")
        check("bad month 422", r.status_code == 422, r.text)
        r = await owner.get("/api/v1/plans/orgs/00000000-0000-0000-0000-000000000000/usage")
        check("usage missing org 404", r.status_code == 404, r.text)

        # ══ 7. Archive vs hard delete ══
        r = await owner.post(f"/api/v1/plans/{test_plan_id}/assign", json={"org_id": org_id})
        check("assign test plan 200", r.status_code == 200, r.text)
        r = await owner.delete(f"/api/v1/plans/{test_plan_id}")
        check("delete assigned plan archives", r.status_code == 200 and r.json()["archived"] is True, r.text)
        r = await owner.get("/api/v1/plans")
        check("archived plan hidden by default", all(p["code"] != "p5-test-plan" for p in r.json()["items"]))
        r = await owner.get("/api/v1/plans?include_inactive=true")
        check("archived plan visible with include_inactive", any(p["code"] == "p5-test-plan" and not p["is_active"] for p in r.json()["items"]))
        r = await owner.post(f"/api/v1/plans/{test_plan_id}/assign", json={"org_id": org_id})
        check("assign archived plan 422", r.status_code == 422, r.text)

        r = await owner.post("/api/v1/plans", json={"code": "p5-scratch", "name": "Scratch", "limits": {}})
        scratch_id = r.json()["plan_id"]
        r = await owner.delete(f"/api/v1/plans/{scratch_id}")
        check("delete unassigned plan hard-deletes", r.status_code == 200 and r.json()["archived"] is False, r.text)
        r = await owner.get(f"/api/v1/plans/{scratch_id}")
        check("hard-deleted plan 404", r.status_code == 404, r.text)

        # ══ 8. RBAC ══
        r = await support.get("/api/v1/plans")
        check("support list plans 403", r.status_code == 403, r.text)
        r = await support.post("/api/v1/plans", json={"code": "x", "name": "x", "limits": {}})
        check("support create plan 403", r.status_code == 403, r.text)
        r = await support.post(f"/api/v1/plans/{starter['id']}/assign", json={"org_id": org_id})
        check("support assign 403", r.status_code == 403, r.text)
        r = await support.get(f"/api/v1/plans/orgs/{org_id}/plan")
        check("support org plan 403", r.status_code == 403, r.text)
        r = await ops.get("/api/v1/plans")
        check("ops list plans 200", r.status_code == 200, r.text)
        r = await ops.post(
            "/api/v1/plans",
            json={"code": "p5-ops-plan", "name": "Ops Made", "limits": {"seats": 2}},
        )
        check("ops create plan 200", r.status_code == 200, r.text)
        r = await ops.delete(f"/api/v1/plans/{r.json()['plan_id']}")
        check("ops delete plan 200", r.status_code == 200, r.text)

        # ══ 9. Audit trail ══
        r = await owner.get("/api/v1/audit?limit=200")
        actions = [a["action"] for a in r.json()]
        check("audit plan.create", "plan.create" in actions)
        check("audit plan.update", "plan.update" in actions)
        check("audit plan.assign", "plan.assign" in actions)
        check("audit plan.archive", "plan.archive" in actions)
        check("audit plan.delete", "plan.delete" in actions)

        # ══ Cleanup ══
        await conn.execute("DELETE FROM public.organizations WHERE id = $1", org_id)
        await conn.execute("DELETE FROM super_admin.plans WHERE code IN ('p5-test-plan', 'p5-scratch', 'p5-ops-plan')")
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

    print(f"\nE2E phase 5: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


asyncio.run(main())
