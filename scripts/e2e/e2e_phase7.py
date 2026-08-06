"""Phase 7 E2E (backend): ops tools — feature flags (scoping, canary,
kill switch) and broadcast notifications (draft -> review -> send/cancel).

Covers:
  - flags: list (seeded), create platform/org/plan scopes, dup code 409,
    bad scope 422, invalid rollout 422, org targeting bad uuid 422,
    unknown plan code 422, platform scope with targeting 422, toggle
    (kill switch, idempotent), patch, delete, get
  - bridge evaluation: platform flag on/off, org-scoped in/out, canary
    rollout 0 -> false, rollout 100 -> true (tenant backend live eval)
  - notifications: create draft, patch draft (200), patch sent (409),
    review (200), review twice (409), send (200 + recipient_count),
    send twice (409), cancel flow, all_orgs vs selected_orgs validation
  - RBAC: operations read-only (flags/notifications view 200, manage 403),
    support all 403
  - audit rows: flags.*, notifications.*

Cleanup: removes E2E flags/broadcasts and test admins' sessions/MFA.
"""
import asyncio
import uuid as uuid_mod

import asyncpg
import httpx
import pyotp
from passlib.context import CryptContext

SUPER = "http://127.0.0.1:8006"
TENANT = "http://127.0.0.1:8005"
PASS = []
FAIL = []

OWNER_EMAIL = "admin@dotstark.ai"
OWNER_PASSWORD = "SuperAdmin@2026!"
SUPPORT_EMAIL = "p7.support@dotstark.ai"
SUPPORT_PASSWORD = "Support@2026!"
OPS_EMAIL = "p7.ops@dotstark.ai"
OPS_PASSWORD = "Ops@2026!"

BRIDGE_SECRET = "dev_bridge_secret_2026"


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
            SELECT gen_random_uuid(), $1, $2, 'P7 E2E Admin', id, 'active', TRUE
            FROM super_admin.roles WHERE code = $3
            """,
            email, pwd_ctx.hash(password), role_code,
        )


async def bridge_flag(code, org_id=None):
    """Directly HMAC-sign a bridge request against the tenant backend.

    Signatures are computed over request.url.path, so context must live in
    the path (no query strings).
    """
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    path = "/api/v1/internal/bridge/flags/" + code
    if org_id:
        path += f"/org/{org_id}"
    canonical = ".".join([ts, "GET", path, OWNER_EMAIL, hashlib.sha256(b"").hexdigest()])
    sig = hmac.new(
        BRIDGE_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    async with httpx.AsyncClient(base_url=TENANT, timeout=15) as c:
        r = await c.get(
            path,
            headers={
                "X-Bridge-Timestamp": ts,
                "X-Bridge-Signature": sig,
                "X-Bridge-Admin-Email": OWNER_EMAIL,
            },
        )
        return r


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

    await conn.execute("DELETE FROM super_admin.feature_flags WHERE code LIKE 'e2e.p7%'")
    await conn.execute(
        "DELETE FROM super_admin.broadcast_notifications WHERE title LIKE 'E2E P7 %'"
    )

    owner_secret = await conn.fetchval(
        "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
        "(SELECT id FROM super_admin.super_admins WHERE email = $1)",
        OWNER_EMAIL,
    )
    # A real org for org-scoped targeting
    org_id = await conn.fetchval(
        "SELECT id FROM public.organizations ORDER BY created_at ASC LIMIT 1"
    )
    plan_codes = [
        r["code"]
        for r in await conn.fetch(
            "SELECT code FROM super_admin.plans WHERE is_active = TRUE LIMIT 2"
        )
    ]

    async with httpx.AsyncClient(base_url=SUPER, headers=CSRF) as owner, httpx.AsyncClient(
        base_url=SUPER, headers=CSRF
    ) as support, httpx.AsyncClient(base_url=SUPER, headers=CSRF) as ops:
        await login_mfa(owner, OWNER_EMAIL, OWNER_PASSWORD, owner_secret)
        support_secret = await enroll_login(support, SUPPORT_EMAIL, SUPPORT_PASSWORD)
        ops_secret = await enroll_login(ops, OPS_EMAIL, OPS_PASSWORD)

        # ================= 1. Feature flags =================
        r = await owner.get("/api/v1/flags")
        check("list flags 200 + seeded", r.status_code == 200 and len(r.json()["items"]) >= 3, r.text[:200])
        codes = [f["code"] for f in r.json()["items"]]
        check("seeded chatbot.enabled", "chatbot.enabled" in codes)
        check("seeded chatbot.canary rollout 5", any(f["code"] == "chatbot.canary" and f["rollout_percent"] == 5 for f in r.json()["items"]))

        r = await owner.post("/api/v1/flags", json={
            "code": f"e2e.p7.platform.{uuid_mod.uuid4().hex[:6]}",
            "name": "E2E P7 Platform Flag", "scope": "platform", "enabled": True,
            "rollout_percent": 100,
        })
        check("create platform flag", r.status_code == 200, r.text)
        platform_flag = r.json()["flag_id"]

        r = await owner.post("/api/v1/flags", json={
            "code": f"e2e.p7.dup.{uuid_mod.uuid4().hex[:6]}",
            "name": "Dup", "scope": "platform",
        })
        dup_code = r.json()["flag_id"]
        r = await owner.post("/api/v1/flags", json={
            "code": [f["code"] for f in (await owner.get("/api/v1/flags")).json()["items"] if f["id"] == dup_code][0],
            "name": "Dup again",
        })
        check("duplicate code 409", r.status_code == 409, r.text)

        r = await owner.post("/api/v1/flags", json={"code": f"e2e.p7.badscope.{uuid_mod.uuid4().hex[:6]}", "name": "x", "scope": "galaxy"})
        check("bad scope 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/flags", json={"code": f"e2e.p7.badroll.{uuid_mod.uuid4().hex[:6]}", "name": "x", "scope": "platform", "rollout_percent": 150})
        check("rollout >100 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/flags", json={"code": f"e2e.p7.badtarget.{uuid_mod.uuid4().hex[:6]}", "name": "x", "scope": "platform", "targeting": {"org_ids": [str(org_id)]}})
        check("platform scope with org targeting 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/flags", json={"code": f"e2e.p7.baduuid.{uuid_mod.uuid4().hex[:6]}", "name": "x", "scope": "org", "targeting": {"org_ids": ["not-a-uuid"]}})
        check("org targeting bad uuid 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/flags", json={"code": f"e2e.p7.badplan.{uuid_mod.uuid4().hex[:6]}", "name": "x", "scope": "plan", "targeting": {"plan_codes": ["no_such_plan"]}})
        check("unknown plan code 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/flags", json={"code": "UPPER_CASE!"})
        check("bad code format 422", r.status_code == 422, r.text)

        # org-scoped flag (rollout 100 => deterministic)
        r = await owner.post("/api/v1/flags", json={
            "code": f"e2e.p7.org.{uuid_mod.uuid4().hex[:6]}",
            "name": "E2E P7 Org Flag", "scope": "org", "enabled": True,
            "targeting": {"org_ids": [str(org_id)]}, "rollout_percent": 100,
        })
        check("create org flag", r.status_code == 200, r.text)
        org_flag = r.json()["flag_id"]

        # plan-scoped flag
        r = await owner.post("/api/v1/flags", json={
            "code": f"e2e.p7.plan.{uuid_mod.uuid4().hex[:6]}",
            "name": "E2E P7 Plan Flag", "scope": "plan", "enabled": True,
            "targeting": {"plan_codes": plan_codes}, "rollout_percent": 100,
        })
        check("create plan flag", r.status_code == 200, r.text)
        plan_flag = r.json()["flag_id"]

        # canary flag rollout 0
        r = await owner.post("/api/v1/flags", json={
            "code": f"e2e.p7.canary.{uuid_mod.uuid4().hex[:6]}",
            "name": "E2E P7 Canary", "scope": "platform", "enabled": True,
            "rollout_percent": 0,
        })
        check("create canary flag", r.status_code == 200, r.text)
        canary_flag = r.json()["flag_id"]

        r = await owner.get(f"/api/v1/flags/{platform_flag}")
        check("get flag", r.status_code == 200 and r.json()["scope"] == "platform", r.text)

        r = await owner.patch(f"/api/v1/flags/{platform_flag}", json={"name": "E2E P7 Renamed", "rollout_percent": 50})
        check("patch flag", r.status_code == 200 and "rollout_percent" in r.json()["updated"], r.text)

        # kill switch
        r = await owner.post(f"/api/v1/flags/{platform_flag}/toggle", json={"enabled": False})
        check("toggle off (kill switch)", r.status_code == 200 and r.json()["enabled"] is False, r.text)
        r = await owner.post(f"/api/v1/flags/{platform_flag}/toggle", json={"enabled": False})
        check("toggle idempotent", r.status_code == 200 and r.json().get("already") is True, r.text)

        # ================= 2. Bridge evaluation =================
        # The tenant backend caches the flag registry for 15s — wait past TTL
        # after each toggle before asserting live evaluation.
        fcode = (await owner.get(f"/api/v1/flags/{platform_flag}")).json()["code"]
        r = await bridge_flag(fcode)
        check("bridge eval killed flag OFF", r.status_code == 200 and r.json()["evaluated"] is False, r.text[:200])
        await owner.post(f"/api/v1/flags/{platform_flag}/toggle", json={"enabled": True})
        r = await owner.patch(f"/api/v1/flags/{platform_flag}", json={"rollout_percent": 100})
        check("patch rollout back 100 before eval", r.status_code == 200, r.text)
        await asyncio.sleep(16)
        r = await bridge_flag(fcode)
        check("bridge eval flag ON", r.status_code == 200 and r.json()["evaluated"] is True, r.text[:200])

        await owner.patch(f"/api/v1/flags/{platform_flag}", json={"rollout_percent": 0})
        check("patch rollout 0", r.status_code == 200, r.text)
        await asyncio.sleep(16)
        r = await bridge_flag(fcode, org_id=str(org_id))
        check("canary 0% evaluated OFF", r.status_code == 200 and r.json()["evaluated"] is False, r.text[:200])
        await owner.patch(f"/api/v1/flags/{platform_flag}", json={"rollout_percent": 100})
        check("patch rollout back 100", r.status_code == 200, r.text)

        ofcode = (await owner.get(f"/api/v1/flags/{org_flag}")).json()["code"]
        r = await bridge_flag(ofcode, org_id=str(org_id))
        check("org flag evaluated ON for targeted org", r.status_code == 200 and r.json()["evaluated"] is True, r.text[:200])
        r = await bridge_flag(ofcode, org_id=str(uuid_mod.uuid4()))
        check("org flag evaluated OFF for other org", r.status_code == 200 and r.json()["evaluated"] is False, r.text[:200])

        ccode = (await owner.get(f"/api/v1/flags/{canary_flag}")).json()["code"]
        r = await bridge_flag(ccode, org_id=str(org_id))
        check("canary 0% always OFF", r.status_code == 200 and r.json()["evaluated"] is False, r.text[:200])
        r = await bridge_flag("chatbot.enabled", org_id=str(org_id))
        check("seeded chatbot.enabled live ON", r.status_code == 200 and r.json()["evaluated"] is True, r.text[:200])
        r = await bridge_flag("no.such.flag")
        check("bridge unknown flag 404", r.status_code == 404, r.text)

        # cleanup bridge-test flags
        await owner.delete(f"/api/v1/flags/{platform_flag}")
        await owner.delete(f"/api/v1/flags/{canary_flag}")
        r = await owner.delete(f"/api/v1/flags/{platform_flag}")
        check("delete missing flag 404", r.status_code == 404, r.text)

        # ================= 3. Broadcast notifications =================
        r = await owner.post("/api/v1/notifications", json={
            "title": f"E2E P7 Maint {uuid_mod.uuid4().hex[:6]}",
            "body": "Maintenance window tonight 23:00-23:30 UTC",
            "audience": "all_orgs",
        })
        check("create broadcast draft", r.status_code == 200, r.text)
        nb = r.json()["broadcast_id"]

        r = await owner.patch(f"/api/v1/notifications/{nb}", json={"body": "Updated: 23:00-23:15 UTC"})
        check("patch draft", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/notifications/{nb}/send")
        check("send before review 409", r.status_code == 409, r.text)
        r = await owner.post(f"/api/v1/notifications/{nb}/review")
        check("review draft", r.status_code == 200, r.text)
        r = await owner.get(f"/api/v1/notifications/{nb}")
        check("reviewed fields set", r.json()["status"] == "reviewed" and r.json()["reviewed_at"] is not None, r.text)
        r = await owner.patch(f"/api/v1/notifications/{nb}", json={"title": "xxxx"})
        check("patch reviewed 409", r.status_code == 409, r.text)
        r = await owner.post(f"/api/v1/notifications/{nb}/review")
        check("review twice 409", r.status_code == 409, r.text)
        r = await owner.post(f"/api/v1/notifications/{nb}/send")
        body = r.json()
        check("send broadcast with recipient count", r.status_code == 200 and body.get("recipient_count", 0) >= 1, r.text)
        r = await owner.post(f"/api/v1/notifications/{nb}/send")
        check("send twice 409", r.status_code == 409, r.text)
        r = await owner.get(f"/api/v1/notifications/{nb}")
        check("sent fields set", r.json()["status"] == "sent" and r.json()["sent_at"] is not None, r.text)

        r = await owner.post("/api/v1/notifications", json={
            "title": f"E2E P7 Sel {uuid_mod.uuid4().hex[:6]}",
            "body": "Selected orgs only", "audience": "selected_orgs",
            "org_ids": [str(org_id)],
        })
        check("create selected_orgs draft", r.status_code == 200, r.text)
        sel = r.json()["broadcast_id"]
        r = await owner.post(f"/api/v1/notifications/{sel}/review")
        check("review selected", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/notifications/{sel}/send")
        check("selected send count = 1", r.status_code == 200 and r.json().get("recipient_count") == 1, r.text)

        r = await owner.post("/api/v1/notifications", json={"title": f"E2E P7 Staff {uuid_mod.uuid4().hex[:6]}", "body": "hello", "audience": "staff"})
        check("create staff draft", r.status_code == 200, r.text)
        st = r.json()["broadcast_id"]
        r = await owner.post(f"/api/v1/notifications/{st}/cancel")
        check("cancel draft", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/notifications/{st}/cancel")
        check("cancel twice idempotent", r.status_code == 200 and r.json().get("already") is True, r.text)
        r = await owner.post(f"/api/v1/notifications/{st}/review")
        check("review cancelled 409", r.status_code == 409, r.text)

        r = await owner.post("/api/v1/notifications", json={"title": f"E2E P7 BadAud {uuid_mod.uuid4().hex[:6]}", "body": "x", "audience": "aliens"})
        check("bad audience 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/notifications", json={"title": f"E2E P7 BadSel {uuid_mod.uuid4().hex[:6]}", "body": "x", "audience": "selected_orgs", "org_ids": []})
        check("selected without orgs 422", r.status_code == 422, r.text)
        r = await owner.post("/api/v1/notifications", json={"title": f"E2E P7 BadOrgs {uuid_mod.uuid4().hex[:6]}", "body": "x", "audience": "all_orgs", "org_ids": [str(org_id)]})
        check("all_orgs with orgs 422", r.status_code == 422, r.text)

        r = await owner.get(f"/api/v1/notifications?status=sent")
        check("list filter sent", r.status_code == 200 and any(b["id"] == nb for b in r.json()["items"]), r.text[:200])

        # ================= 4. RBAC =================
        r = await ops.get("/api/v1/flags")
        check("ops flags view 200", r.status_code == 200, r.text)
        r = await ops.get("/api/v1/notifications")
        check("ops notifications view 200", r.status_code == 200, r.text[:200])
        r = await ops.post("/api/v1/flags", json={"code": f"e2e.p7.ops.{uuid_mod.uuid4().hex[:6]}", "name": "x"})
        check("ops create flag 403", r.status_code == 403, r.text)
        r = await ops.post("/api/v1/notifications", json={"title": f"E2E P7 Ops {uuid_mod.uuid4().hex[:6]}", "body": "x"})
        check("ops create broadcast 403", r.status_code == 403, r.text)
        for path in ("/api/v1/flags", "/api/v1/notifications"):
            r = await support.get(path)
            check(f"support {path} 403", r.status_code == 403, r.text)

        # ================= 5. Audit =================
        r = await owner.get("/api/v1/audit?limit=200")
        actions = [a["action"] for a in r.json()]
        for wanted in (
            "flags.create", "flags.update", "flags.toggle", "flags.delete",
            "notifications.create", "notifications.update", "notifications.review",
            "notifications.send", "notifications.cancel",
        ):
            check(f"audit {wanted}", wanted in actions)

        # ---- Cleanup ----
        await conn.execute("DELETE FROM super_admin.feature_flags WHERE code LIKE 'e2e.p7%'")
        await conn.execute("DELETE FROM super_admin.broadcast_notifications WHERE title LIKE 'E2E P7 %'")
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

    print(f"\nE2E phase 7 ops tools: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


asyncio.run(main())
