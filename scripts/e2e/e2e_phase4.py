"""Phase 4 E2E: lifecycle actions + two-person approvals.

Covers:
  - /lifecycle org create/suspend/restore/config + user disable/enable
  - RBAC 403 for a support-role admin on all write routes + approvals
  - /approvals: request -> self-approve blocked -> second admin approves
    -> bridge executes the org delete -> EXECUTED
  - reject path (org survives), 24h expiry path (409), bad request_type (422)
  - tenant-side audit rows survive org deletion; control-plane chain stays valid

Cleanup: removes the second test admin and all E2E orgs/approvals.
"""
import asyncio
import hashlib
import json
import uuid as uuid_mod

import asyncpg
import httpx
import pyotp
from passlib.context import CryptContext

SUPER = "http://127.0.0.1:8006"
PASS = []
FAIL = []

OWNER_EMAIL = "admin@dotstark.ai"
OWNER_PASSWORD = "SuperAdmin@2026!"
SECOND_EMAIL = "p4.second.owner@dotstark.ai"
SECOND_PASSWORD = "SecondOwner@2026!"
SUPPORT_EMAIL = "p4.support@dotstark.ai"
SUPPORT_PASSWORD = "Support@2026!"


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


async def mint_admin(conn, email, password, role_code="owner"):
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    admin_id = await conn.fetchval(
        """
        INSERT INTO super_admin.super_admins
            (id, email, password_hash, full_name, role_id, status, mfa_required)
        SELECT gen_random_uuid(), $1, $2, 'P4 E2E Admin', id, 'active', TRUE
        FROM super_admin.roles WHERE code = $3
        RETURNING id
        """,
        email, pwd_ctx.hash(password), role_code,
    )
    return admin_id


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark@localhost:5432/dotstark_v2"
    )

    CSRF = {"X-Requested-With": "XMLHttpRequest"}

    # Idempotency: reset leftovers of earlier runs (sessions/MFA/login
    # attempts). Test admins themselves are kept forever — audit_logs has
    # ON DELETE SET NULL + an append-only trigger, so deleting an actor is
    # impossible by design; instead we refresh the password + MFA each run.
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    for email, password in ((SECOND_EMAIL, SECOND_PASSWORD), (SUPPORT_EMAIL, SUPPORT_PASSWORD)):
        row = await conn.fetchrow(
            "SELECT id, role_id FROM super_admin.super_admins WHERE email = $1", email
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
            await mint_admin(conn, email, password, "owner" if "owner" in email else "support")

    # ── Mint the second owner + a support-role admin ──────────────────────
    second_id = await conn.fetchval(
        "SELECT id FROM super_admin.super_admins WHERE email = $1", SECOND_EMAIL
    )
    support_id = await conn.fetchval(
        "SELECT id FROM super_admin.super_admins WHERE email = $1", SUPPORT_EMAIL
    )

    async with httpx.AsyncClient(base_url=SUPER, headers=CSRF) as c, httpx.AsyncClient(
        base_url=SUPER, headers=CSRF
    ) as c2, httpx.AsyncClient(base_url=SUPER, headers=CSRF) as cs:

        # ── Owner: login + MFA ──
        owner_secret = await conn.fetchval(
            "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
            "(SELECT id FROM super_admin.super_admins WHERE email = $1)",
            OWNER_EMAIL,
        )
        await login_mfa(c, OWNER_EMAIL, OWNER_PASSWORD, owner_secret)
        r = await c.post(
            "/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(owner_secret).now()}
        )
        check("owner stepup 200", r.status_code == 200, r.text)

        # ── Second owner: login -> enroll MFA -> verify ──
        r = await c2.post(
            "/api/v1/auth/login", json={"email": SECOND_EMAIL, "password": SECOND_PASSWORD}
        )
        check("second login 200", r.status_code == 200, r.text)
        r = await c2.post("/api/v1/auth/mfa/enroll")
        check("second enroll 200", r.status_code == 200, r.text)
        second_secret = r.json()["secret"]
        r = await c2.post(
            "/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(second_secret).now()}
        )
        check("second mfa verify 200", r.status_code == 200, r.text)
        r = await c2.post(
            "/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(second_secret).now()}
        )
        check("second stepup 200", r.status_code == 200, r.text)

        # ── Support: login + enroll + verify ──
        r = await cs.post(
            "/api/v1/auth/login", json={"email": SUPPORT_EMAIL, "password": SUPPORT_PASSWORD}
        )
        check("support login 200", r.status_code == 200, r.text)
        r = await cs.post("/api/v1/auth/mfa/enroll")
        support_secret = r.json()["secret"]
        await cs.post(
            "/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(support_secret).now()}
        )

        # ══ 1. Lifecycle: create org ══
        org_name = f"E2E P4 Lifecycle {uuid_mod.uuid4().hex[:8]}"
        r = await c.post(
            "/api/v1/lifecycle/orgs",
            json={"name": org_name, "business_name": "Lifecycle Test Co"},
        )
        check("lifecycle create 200", r.status_code == 200, r.text)
        body = r.json()
        org_id = body.get("org_id")
        check("create returns org_id+api_key", bool(org_id) and body.get("api_key", "").startswith("ds_"), body)
        row = await conn.fetchrow(
            "SELECT is_active FROM organizations WHERE id = $1", org_id
        )
        check("org row exists active", row and row["is_active"] is True, row)

        # ── suspend → DB false ──
        r = await c.post(f"/api/v1/lifecycle/orgs/{org_id}/suspend")
        check("suspend 200", r.status_code == 200, r.text)
        row = await conn.fetchrow(
            "SELECT is_active FROM organizations WHERE id = $1", org_id
        )
        check("org inactive after suspend", row and row["is_active"] is False, row)

        # ── restore → DB true ──
        r = await c.post(f"/api/v1/lifecycle/orgs/{org_id}/restore")
        check("restore 200", r.status_code == 200, r.text)
        row = await conn.fetchrow(
            "SELECT is_active FROM organizations WHERE id = $1", org_id
        )
        check("org active after restore", row and row["is_active"] is True, row)

        # ── config patch → DB updated ──
        r = await c.patch(
            f"/api/v1/lifecycle/orgs/{org_id}/config",
            json={"timezone": "America/New_York", "require_otp": True},
        )
        check("config patch 200", r.status_code == 200, r.text)
        check("config updated list", r.json().get("updated") == ["timezone", "require_otp"], r.text)
        cfg = await conn.fetchrow(
            "SELECT timezone, require_otp FROM org_configs WHERE org_id = $1", org_id
        )
        check(
            "config applied in DB",
            cfg and cfg["timezone"] == "America/New_York" and cfg["require_otp"] is True,
            cfg,
        )

        # ── user disable/enable (real user, toggled back) ──
        u = await conn.fetchrow("SELECT id, is_active FROM users ORDER BY created_at LIMIT 1")
        user_id = u["id"]
        r = await c.post(f"/api/v1/lifecycle/users/{user_id}/disable")
        check("user disable 200", r.status_code == 200, r.text)
        row = await conn.fetchrow("SELECT is_active FROM users WHERE id = $1", user_id)
        check("user inactive", row and row["is_active"] is False, row)
        r = await c.post(f"/api/v1/lifecycle/users/{user_id}/enable")
        check("user enable 200", r.status_code == 200, r.text)
        row = await conn.fetchrow("SELECT is_active FROM users WHERE id = $1", user_id)
        check("user active again", row and row["is_active"] is True, row)
        r = await c.post(f"/api/v1/lifecycle/users/{uuid_mod.uuid4()}/disable")
        check("unknown user 404", r.status_code == 404)

        # ── lifecycle audit entries on control plane ──
        r = await c.get("/api/v1/audit", params={"actor_email": OWNER_EMAIL, "limit": 30})
        actions = {a["action"] for a in r.json()}
        needed = {"org.create", "org.suspend", "org.restore", "org.config.update", "user.disable", "user.enable"}
        check("lifecycle audit actions present", needed.issubset(actions), actions)

        # ══ 2. RBAC: support admin blocked everywhere ══
        r = await cs.post(f"/api/v1/lifecycle/orgs/{org_id}/suspend")
        check("support suspend 403", r.status_code == 403, r.text)
        r = await cs.post("/api/v1/lifecycle/orgs", json={"name": "Nope"})
        check("support create 403", r.status_code == 403, r.text)
        r = await cs.post(
            "/api/v1/approvals",
            json={"request_type": "org.delete", "resource_id": org_id, "payload": {}},
        )
        check("support approval request 403", r.status_code == 403, r.text)

        # ══ 3. Approvals: 2-person delete ══
        r = await c.post(
            "/api/v1/approvals",
            json={"request_type": "org.delete", "resource_id": org_id, "payload": {"name": org_name}},
        )
        check("approval request 200", r.status_code == 200, r.text)
        approval_id = r.json()["approval_id"]

        r = await c.post(f"/api/v1/approvals/{approval_id}/approve", json={"note": "self"})
        check("self-approve 403", r.status_code == 403, r.text)

        r = await c.get("/api/v1/approvals", params={"status": "PENDING"})
        items = r.json()["items"]
        check("approval listed PENDING", any(i["id"] == str(approval_id) and i["status"] == "PENDING" for i in items), items)

        # second owner approves → bridge executes delete
        r = await c2.post(
            f"/api/v1/approvals/{approval_id}/approve", json={"note": "approved in e2e"}
        )
        check("second-person approve 200", r.status_code == 200, r.text)
        row = await conn.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
        check("org deleted from DB", row is None)
        tenant_audit = await conn.fetchval(
            "SELECT count(*) FROM public.audit_logs WHERE resource_id = $1", str(org_id)
        )
        check("tenant audit rows survive delete", tenant_audit >= 4, tenant_audit)
        st = await conn.fetchval(
            "SELECT status FROM super_admin.approvals WHERE id = $1", approval_id
        )
        check("approval EXECUTED", st == "EXECUTED", st)

        # ── reject path: second org survives ──
        org2_name = f"E2E P4 Reject {uuid_mod.uuid4().hex[:8]}"
        r = await c.post(
            "/api/v1/lifecycle/orgs", json={"name": org2_name, "is_active": True}
        )
        org2_id = r.json()["org_id"]
        r = await c2.post(
            "/api/v1/approvals",
            json={"request_type": "org.delete", "resource_id": org2_id, "payload": {}},
        )
        a2 = r.json()["approval_id"]
        r = await c.post(f"/api/v1/approvals/{a2}/reject", json={"note": "denied"})
        check("reject 200", r.status_code == 200, r.text)
        st = await conn.fetchval(
            "SELECT status FROM super_admin.approvals WHERE id = $1", a2
        )
        check("approval REJECTED", st == "REJECTED", st)
        row = await conn.fetchrow("SELECT id FROM organizations WHERE id = $1", org2_id)
        check("rejected org survives", row is not None)

        # ── expiry path ──
        expired_id = await conn.fetchval(
            """
            INSERT INTO super_admin.approvals
                (id, request_type, resource_type, resource_id, payload, status,
                 requested_by_id, requested_by_email, expires_at)
            VALUES (gen_random_uuid(), 'org.delete', 'organization', $1,
                    '{}', 'PENDING', $2, $3, now() - interval '1 hour')
            RETURNING id
            """,
            str(org2_id),
            second_id,
            SECOND_EMAIL,
        )
        r = await c.post(f"/api/v1/approvals/{expired_id}/approve", json={})
        check("expired approve 409", r.status_code == 409, r.text)
        st = await conn.fetchval(
            "SELECT status FROM super_admin.approvals WHERE id = $1", expired_id
        )
        check("expired marked EXPIRED", st == "EXPIRED", st)

        # ── bad request_type ──
        r = await c.post(
            "/api/v1/approvals",
            json={"request_type": "org.explode", "resource_id": org2_id, "payload": {}},
        )
        check("unsupported request_type 422", r.status_code == 422, r.text)

        # ── audit chain still valid after all writes ──
        r = await c.get("/api/v1/audit/verify")
        v = r.json()
        check("audit chain valid", v.get("chain_valid") is True, v)

    # ── Cleanup ──
    # Test admins are intentionally KEPT (append-only audit chain forbids
    # deleting actors); their sessions/MFA are dropped and status set disabled
    # so they cannot be used after the run.
    await conn.execute("DELETE FROM organizations WHERE id = $1", org2_id)
    await conn.execute("DELETE FROM super_admin.approvals")
    for admin_id in (second_id, support_id):
        await conn.execute(
            "DELETE FROM super_admin.admin_sessions WHERE admin_id = $1", admin_id
        )
        await conn.execute(
            "DELETE FROM super_admin.admin_mfa WHERE admin_id = $1", admin_id
        )
        await conn.execute(
            "UPDATE super_admin.super_admins SET status = 'disabled' WHERE id = $1",
            admin_id,
        )
    await conn.close()

    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("FAILED:", FAIL)
        raise SystemExit(1)


asyncio.run(main())
