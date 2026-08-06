"""Phase 5 E2E (backend part 2): billing — subscriptions, invoices, refund
safeguards (MFA step-up + approval threshold) and revenue reports.

Covers:
  - subscription create (409 on duplicate, 422 on archived plan)
  - invoice issue + retry (409 on non-open)
  - refund < threshold: 403 without step-up, 401 bad code, refunded with
    step-up, 409 on second refund
  - refund > threshold: PENDING_APPROVAL, self-approve 403, second admin
    approves -> EXECUTED -> invoice refunded
  - RBAC: support (view-only) 403s, operations can retry but 403 on refund
  - revenue report + CSV: MRR/ARR/churn/aging
  - audit rows for every billing action + step-up

Cleanup: removes E2E orgs (cascade wipes subscriptions/invoices), refund
approvals and the test admins' MFA/sessions (actors are kept, status disabled).
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
SECOND_EMAIL = "p5.second.owner@dotstark.ai"
SECOND_PASSWORD = "SecondOwner@2026!"
SUPPORT_EMAIL = "p4.support@dotstark.ai"
SUPPORT_PASSWORD = "Support@2026!"
OPS_EMAIL = "p5.ops@dotstark.ai"
OPS_PASSWORD = "Ops@2026!"

THRESHOLD = 50000  # REFUND_APPROVAL_THRESHOLD_CENTS default


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
            SELECT gen_random_uuid(), $1, $2, 'P5 E2E Admin', id, 'active', TRUE
            FROM super_admin.roles WHERE code = $3
            """,
            email, pwd_ctx.hash(password), role_code,
        )


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark@localhost:5432/dotstark_v2"
    )

    CSRF = {"X-Requested-With": "XMLHttpRequest"}

    # Idempotent setup
    for email, password, role in (
        (SECOND_EMAIL, SECOND_PASSWORD, "owner"),
        (SUPPORT_EMAIL, SUPPORT_PASSWORD, "support"),
        (OPS_EMAIL, OPS_PASSWORD, "operations"),
    ):
        await reset_admin(conn, email, password, role)
    await conn.execute("DELETE FROM public.organizations WHERE name LIKE 'E2E P5 Billing %'")
    await conn.execute("DELETE FROM super_admin.approvals WHERE request_type = 'billing.refund'")
    await conn.execute(
        "DELETE FROM super_admin.plans WHERE code IN ('p5-archive-me', 'p5-scratch')"
    )

    owner_secret = await conn.fetchval(
        "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
        "(SELECT id FROM super_admin.super_admins WHERE email = $1)",
        OWNER_EMAIL,
    )

    async with httpx.AsyncClient(base_url=SUPER, headers=CSRF) as owner, httpx.AsyncClient(
        base_url=SUPER, headers=CSRF
    ) as second, httpx.AsyncClient(base_url=SUPER, headers=CSRF) as support, httpx.AsyncClient(
        base_url=SUPER, headers=CSRF
    ) as ops:
        await login_mfa(owner, OWNER_EMAIL, OWNER_PASSWORD, owner_secret)
        second_secret = await enroll_login(second, SECOND_EMAIL, SECOND_PASSWORD)
        support_secret = await enroll_login(support, SUPPORT_EMAIL, SUPPORT_PASSWORD)
        ops_secret = await enroll_login(ops, OPS_EMAIL, OPS_PASSWORD)

        # ══ 1. Setup: orgs + plans ══
        r = await owner.post(
            "/api/v1/lifecycle/orgs",
            json={"name": f"E2E P5 Billing {uuid_mod.uuid4().hex[:8]}", "business_name": "Billing Test Co"},
        )
        check("create org 200", r.status_code == 200, r.text)
        org_id = r.json()["org_id"]
        r = await owner.get("/api/v1/plans")
        plans = {p["code"]: p for p in r.json()["items"]}
        pro = plans["pro"]
        starter = plans["starter"]

        # ══ 2. Subscription ══
        r = await owner.post(
            "/api/v1/billing/subscriptions",
            json={"org_id": org_id, "plan_id": pro["id"]},
        )
        check("create subscription 200", r.status_code == 200, r.text)
        r = await owner.post(
            "/api/v1/billing/subscriptions",
            json={"org_id": org_id, "plan_id": starter["id"]},
        )
        check("duplicate subscription 409", r.status_code == 409, r.text)

        # archived plan -> 422 on subscribe
        r = await owner.post("/api/v1/plans", json={"code": "p5-archive-me", "name": "Archive Me", "limits": {}})
        arch_plan_id = r.json()["plan_id"]
        r = await owner.post("/api/v1/lifecycle/orgs", json={"name": f"E2E P5 Billing Arch {uuid_mod.uuid4().hex[:8]}"})
        arch_org_id = r.json()["org_id"]
        await owner.post(f"/api/v1/plans/{arch_plan_id}/assign", json={"org_id": arch_org_id})
        await owner.delete(f"/api/v1/plans/{arch_plan_id}")
        r = await owner.post(
            "/api/v1/billing/subscriptions",
            json={"org_id": arch_org_id, "plan_id": arch_plan_id},
        )
        check("subscribe archived plan 422", r.status_code == 422, r.text)

        r = await owner.get(f"/api/v1/billing/subscriptions?org_id={org_id}")
        check("list subscriptions", r.status_code == 200 and len(r.json()["items"]) == 1, r.text)

        # ══ 3. Invoices + retry ══
        r = await owner.post(
            f"/api/v1/billing/invoices?org_id={org_id}",
            json={"amount_cents": 20000, "description": "Monthly"},
        )
        check("issue invoice 200", r.status_code == 200, r.text)
        small_inv = r.json()["invoice_id"]
        r = await owner.post(
            f"/api/v1/billing/invoices?org_id={org_id}",
            json={"amount_cents": THRESHOLD + 10000, "description": "Big one"},
        )
        big_inv = r.json()["invoice_id"]
        r = await owner.post(
            f"/api/v1/billing/invoices?org_id={org_id}",
            json={"amount_cents": 0},
        )
        check("issue zero invoice 422", r.status_code == 422, r.text)

        r = await owner.post(f"/api/v1/billing/invoices/{small_inv}/retry")
        check("retry invoice -> paid", r.status_code == 200 and r.json()["status"] == "paid", r.text)
        r = await owner.post(f"/api/v1/billing/invoices/{small_inv}/retry")
        check("retry non-open 409", r.status_code == 409, r.text)
        r = await owner.post(f"/api/v1/billing/invoices/{big_inv}/retry")
        check("retry big invoice -> paid", r.status_code == 200, r.text)

        # ══ 4. Refund < threshold: step-up required ══
        r = await owner.post(f"/api/v1/billing/invoices/{small_inv}/refund", json={"reason": "test"})
        check("refund without stepup 403", r.status_code == 403, r.text)
        r = await owner.post("/api/v1/auth/mfa/stepup", json={"code": "000000"})
        check("stepup wrong code 401", r.status_code == 401, r.text)
        r = await owner.post("/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(owner_secret).now()})
        check("stepup 200", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/billing/invoices/{small_inv}/refund", json={"reason": "courtesy"})
        check("refund small (stepup) -> refunded", r.status_code == 200 and r.json()["status"] == "refunded", r.text)
        r = await owner.post(f"/api/v1/billing/invoices/{small_inv}/refund", json={"reason": "again"})
        check("refund already refunded 409", r.status_code == 409, r.text)

        # ══ 5. Refund > threshold: approval workflow ══
        r = await owner.post("/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(owner_secret).now()})
        check("stepup again 200", r.status_code == 200, r.text)
        r = await owner.post(f"/api/v1/billing/invoices/{big_inv}/refund", json={"reason": "over threshold"})
        check("refund big -> PENDING_APPROVAL", r.status_code == 200 and r.json()["status"] == "PENDING_APPROVAL", r.text)
        approval_id = r.json()["approval_id"]
        check("approval id present", bool(approval_id))

        r = await owner.post(f"/api/v1/approvals/{approval_id}/approve", json={"note": "self"})
        check("self-approve 403", r.status_code == 403, r.text)

        r = await second.post(f"/api/v1/approvals/{approval_id}/approve", json={"note": "looks fine"})
        check("second admin approve -> EXECUTED", r.status_code == 200 and r.json()["status"] == "EXECUTED", r.text)
        r = await owner.get("/api/v1/billing/invoices")
        big = next(i for i in r.json()["items"] if i["id"] == big_inv)
        check("big invoice refunded by approval", big["status"] == "refunded" and big["refund_amount_cents"] == THRESHOLD + 10000, str(big))

        # ══ 6. RBAC ══
        r = await support.get("/api/v1/billing/invoices")
        check("support view invoices 200", r.status_code == 200, r.text)
        r = await support.post(f"/api/v1/billing/invoices/{small_inv}/retry")
        check("support retry 403", r.status_code == 403, r.text)
        r = await support.post(f"/api/v1/billing/invoices/{small_inv}/refund", json={"reason": "x"})
        check("support refund 403", r.status_code == 403, r.text)
        r = await support.post("/api/v1/billing/subscriptions", json={"org_id": org_id, "plan_id": pro["id"]})
        check("support create subscription 403", r.status_code == 403, r.text)
        r = await ops.get("/api/v1/billing/reports/revenue")
        check("ops reports 200", r.status_code == 200, r.text)
        r = await ops.post(f"/api/v1/billing/invoices/{big_inv}/refund", json={"reason": "x"})
        check("ops refund 403 (no billing.refund)", r.status_code == 403, r.text)

        # ══ 7. Revenue report + aging + churn ══
        await conn.execute(
            """
            INSERT INTO super_admin.subscriptions
                (org_id, plan_id, provider, status, current_period_start, current_period_end, canceled_at)
            SELECT $1, id, 'mock', 'canceled', now() - interval '60 days', now() - interval '30 days',
                   now() - interval '10 days'
            FROM super_admin.plans WHERE code = 'starter'
            """,
            arch_org_id,
        )
        await conn.execute(
            """
            INSERT INTO super_admin.invoices (org_id, provider, provider_invoice_id, number, amount_cents, status, due_at)
            VALUES ($1, 'mock', 'in_aging_1', 'INV-AGING-1', 150000, 'open', now() - interval '90 days')
            """,
            org_id,
        )
        r = await owner.get("/api/v1/billing/reports/revenue")
        rep = r.json()
        check("report mrr = pro price", rep["mrr_cents"] == pro["monthly_price_cents"], str(rep))
        check("report arr = 12x mrr", rep["arr_cents"] == pro["monthly_price_cents"] * 12)
        check("report churn 50%", rep["churn_rate_pct"] == 50.0, str(rep["churn_rate_pct"]))
        check("report aging 90+", rep["invoice_aging"]["90_plus_days"] == 1, str(rep["invoice_aging"]))
        check("report open total", rep["invoice_aging"]["open_total_cents"] == 150000, str(rep["invoice_aging"]))

        r = await owner.get("/api/v1/billing/reports/revenue.csv")
        check("csv content type", "text/csv" in r.headers.get("content-type", ""), r.headers.get("content-type"))
        check("csv contains mrr", "mrr_cents" in r.text and str(pro["monthly_price_cents"]) in r.text, r.text[:200])
        r = await support.get("/api/v1/billing/reports/revenue.csv")
        check("support csv 200", r.status_code == 200, r.text)

        # ══ 8. Audit ══
        r = await owner.get("/api/v1/audit?limit=200")
        actions = [a["action"] for a in r.json()]
        for wanted in (
            "billing.subscription.create",
            "billing.invoice.issue",
            "billing.invoice.retry",
            "billing.refund.executed",
            "billing.refund.request",
            "auth.stepup",
        ):
            check(f"audit {wanted}", wanted in actions)

        # ══ Cleanup ══
        await conn.execute("DELETE FROM public.organizations WHERE id = ANY($1::uuid[])", [org_id, arch_org_id])
        await conn.execute("DELETE FROM super_admin.approvals WHERE request_type = 'billing.refund'")
        for email in (SECOND_EMAIL, SUPPORT_EMAIL, OPS_EMAIL):
            row = await conn.fetchrow(
                "SELECT id FROM super_admin.super_admins WHERE email = $1", email
            )
            await conn.execute("DELETE FROM super_admin.admin_sessions WHERE admin_id = $1", row["id"])
            await conn.execute("DELETE FROM super_admin.admin_mfa WHERE admin_id = $1", row["id"])
            await conn.execute(
                "UPDATE super_admin.super_admins SET status = 'disabled' WHERE id = $1",
                row["id"],
            )

    print(f"\nE2E phase 5 billing: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


asyncio.run(main())
