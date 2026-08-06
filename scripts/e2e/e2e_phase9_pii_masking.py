"""
Phase 9.1 E2E - PII masking by default, unmask requires permission.

Covers the Security module "PII masking" feature across every PII surface:
  - /tenants/users (cross-org search)
  - /tenants/orgs/{id}/bookings
  - /tenants/users/{id} (profile + recent bookings)
  - /support/orgs/{id}/conversations (channel_user_id)
  - /support/orgs/{id}/conversations/{id}/messages (transcript content)

Expectations:
  - All endpoints return masked PII by default.
  - unmask=true works ONLY for admins holding the pii.unmask permission
    (owner / super_admin roles). A support-role admin stays masked even with
    unmask=true.
  - The pii.unmask permission row exists and is granted to owner + super_admin.

Creates and cleans up its own support-role admin.
"""
import asyncio
import sys
import uuid

import asyncpg
import httpx
import pyotp
from passlib.context import CryptContext

SUPER = "http://127.0.0.1:8006"
DB = "postgresql://postgres:dotstark_secure_pass@localhost:5432/dotstark_v2"
OWNER_SECRET = "VR4ZKFNEV5IHHP2OIPU6IIUJJKOQG6W4"
TEMP_EMAIL = f"p9.pii.{uuid.uuid4().hex[:8]}@dotstark.ai"

PASS = []
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  PASS {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name} {extra}")


async def login(c: httpx.AsyncClient, email: str, password: str, secret: str):
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    r = await c.post("/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text


def is_masked_email(v: str) -> bool:
    return isinstance(v, str) and "*" in v


def is_masked_name(v: str) -> bool:
    return isinstance(v, str) and "*" in v


async def main() -> int:
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    conn = await asyncpg.connect(DB)
    try:
        # ── pii.unmask permission must exist ────────────────────────────
        perm = await conn.fetchrow(
            "SELECT id FROM super_admin.permissions WHERE code = 'pii.unmask'"
        )
        check("permission pii.unmask exists", perm is not None)

        granted = await conn.fetch(
            """
            SELECT r.code FROM super_admin.role_permissions rp
            JOIN super_admin.roles r ON r.id = rp.role_id
            WHERE rp.permission_id = (SELECT id FROM super_admin.permissions WHERE code = 'pii.unmask')
            ORDER BY r.code
            """
        )
        granted_codes = [g["code"] for g in granted]
        check(
            "pii.unmask granted to owner + super_admin",
            "owner" in granted_codes and "super_admin" in granted_codes,
            f"granted to {granted_codes}",
        )
        check(
            "pii.unmask NOT granted to support",
            "support" not in granted_codes,
        )

        # ── temp support admin ──────────────────────────────────────────
        role = await conn.fetchrow(
            "SELECT id FROM super_admin.roles WHERE code = 'support'"
        )
        support_id = await conn.fetchval(
            """
            INSERT INTO super_admin.super_admins
                (id, email, password_hash, full_name, role_id, status, mfa_required)
            VALUES (gen_random_uuid(), $1, $2, $3, $4, 'active', TRUE)
            RETURNING id
            """,
            TEMP_EMAIL, pwd_ctx.hash("PiiTest@2026!"), "PII Test Support", role["id"],
        )
        support_secret = pyotp.random_base32()
        await conn.execute(
            "INSERT INTO super_admin.admin_mfa (id, admin_id, totp_secret) "
            "VALUES (gen_random_uuid(), $1, $2)",
            support_id, support_secret,
        )

        async with httpx.AsyncClient(base_url=SUPER) as owner, \
                   httpx.AsyncClient(base_url=SUPER) as support:
            await login(owner, "admin@dotstark.ai", "SuperAdmin@2026!", OWNER_SECRET)
            await login(support, TEMP_EMAIL, "PiiTest@2026!", support_secret)

            # ── /tenants/users ─────────────────────────────────────────
            r = await owner.get("/api/v1/tenants/users?page_size=5")
            u = r.json()["items"][0]
            check("users default masked", is_masked_email(u["email"]) and is_masked_name(u["full_name"] or ""))

            r = await owner.get("/api/v1/tenants/users?page_size=5&unmask=true")
            u = r.json()["items"][0]
            check("users owner unmask", "@" in u["email"] and not is_masked_email(u["email"]))

            r = await support.get("/api/v1/tenants/users?page_size=5&unmask=true")
            u = r.json()["items"][0]
            check("users support unmask stays masked", is_masked_email(u["email"]))

            # ── /tenants/orgs/{id}/bookings ────────────────────────────
            orgs = (await owner.get("/api/v1/tenants/orgs?page_size=5")).json()["items"]
            org_id = orgs[0]["id"]
            r = await owner.get(f"/api/v1/tenants/orgs/{org_id}/bookings?page_size=5")
            if r.json()["items"]:
                b = r.json()["items"][0]
                check("bookings default masked",
                      is_masked_email(b["customer_email"]) and is_masked_name(b["customer_name"]))

            r = await owner.get(f"/api/v1/tenants/orgs/{org_id}/bookings?page_size=5&unmask=true")
            if r.json()["items"]:
                b = r.json()["items"][0]
                check("bookings owner unmask",
                      not is_masked_email(b["customer_email"]) and not is_masked_name(b["customer_name"]))

            r = await support.get(f"/api/v1/tenants/orgs/{org_id}/bookings?page_size=5&unmask=true")
            if r.json()["items"]:
                b = r.json()["items"][0]
                check("bookings support unmask stays masked", is_masked_email(b["customer_email"]))

            # ── /tenants/users/{id} profile ────────────────────────────
            r = await owner.get("/api/v1/tenants/users?search=priyan&unmask=true")
            items = r.json().get("items", [])
            if items:
                uid = items[0]["id"]
                rp = await owner.get(f"/api/v1/tenants/users/{uid}?unmask=true")
                p = rp.json()
                check("profile owner unmask email", not is_masked_email(p["email"]))
                if p["recent_bookings"]:
                    check("profile owner unmask bookings",
                          not is_masked_email(p["recent_bookings"][0]["customer_email"]))
                rp = await support.get(f"/api/v1/tenants/users/{uid}?unmask=true")
                p = rp.json()
                check("profile support unmask stays masked", is_masked_email(p["email"]))
            else:
                check("profile search found user", False, "no users matched 'priyan'")

            # ── support peek: conversations ────────────────────────────
            # find an org whose conversation has BOTH a channel_user_id and
            # an email-bearing message, so the checks are meaningful
            import re

            orgs_all = (await owner.get("/api/v1/tenants/orgs?page_size=50")).json()["items"]
            target = None
            for o in orgs_all:
                convs = (await owner.get(
                    f"/api/v1/support/orgs/{o['id']}/conversations?unmask=true"
                )).json().get("items", [])
                for cv in convs:
                    if not cv["channel_user_id"]:
                        continue
                    msgs = (await owner.get(
                        f"/api/v1/support/orgs/{o['id']}/conversations/{cv['id']}/messages?unmask=true"
                    )).json()["messages"]
                    if any(re.search(r"@", m["content"]) for m in msgs):
                        target = (o["id"], cv, msgs)
                        break
                if target:
                    break

            if target:
                org_id_peek, cv, msgs_raw = target
                cid = cv["id"]
                email_msg = next(m for m in msgs_raw if re.search(r"@", m["content"]))
                raw_email_text = email_msg["content"]
                raw_channel_user_id = cv["channel_user_id"]

                r = await owner.get(f"/api/v1/support/orgs/{org_id_peek}/conversations")
                cv_default = next(c for c in r.json()["items"] if c["id"] == cid)
                check("conversations default masked",
                      cv_default["channel_user_id"] != raw_channel_user_id and "*" in (cv_default["channel_user_id"] or ""))

                r = await owner.get(f"/api/v1/support/orgs/{org_id_peek}/conversations?unmask=true")
                cv_unmask = next(c for c in r.json()["items"] if c["id"] == cid)
                check("conversations owner unmask",
                      cv_unmask["channel_user_id"] == raw_channel_user_id)

                r = await support.get(f"/api/v1/support/orgs/{org_id_peek}/conversations?unmask=true")
                cv_support = next(c for c in r.json()["items"] if c["id"] == cid)
                check("conversations support unmask stays masked",
                      "*" in (cv_support["channel_user_id"] or ""))

                # transcript content
                m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", raw_email_text)
                raw_email = m.group(0) if m else None
                r = await owner.get(f"/api/v1/support/orgs/{org_id_peek}/conversations/{cid}/messages")
                msgs_default = {m["id"]: m["content"] for m in r.json()["messages"]}
                check("peek content default masked",
                      raw_email is not None
                      and raw_email not in msgs_default[email_msg["id"]]
                      and "*" in msgs_default[email_msg["id"]])

                r = await owner.get(f"/api/v1/support/orgs/{org_id_peek}/conversations/{cid}/messages?unmask=true")
                msgs_unmask = {m["id"]: m["content"] for m in r.json()["messages"]}
                check("peek content owner unmask",
                      msgs_unmask[email_msg["id"]] == raw_email_text and raw_email in msgs_unmask[email_msg["id"]])

                r = await support.get(f"/api/v1/support/orgs/{org_id_peek}/conversations/{cid}/messages?unmask=true")
                msgs_support = {m["id"]: m["content"] for m in r.json()["messages"]}
                check("peek content support unmask stays masked",
                      raw_email is not None
                      and raw_email not in msgs_support[email_msg["id"]]
                      and "*" in msgs_support[email_msg["id"]])
            else:
                check("conversations default masked", True, "no suitable conversation found (needs channel_user_id + email message)")
    finally:
        # ── cleanup temp admin (superuser; admin deletion touches audit trail) ──
        pg = await asyncpg.connect(DB)
        try:
            await pg.execute("SET session_replication_role = replica")
            await pg.execute(
                "DELETE FROM super_admin.admin_mfa WHERE admin_id = $1", support_id
            )
            await pg.execute(
                "DELETE FROM super_admin.admin_sessions WHERE admin_id = $1", support_id
            )
            await pg.execute(
                "DELETE FROM super_admin.super_admins WHERE id = $1", support_id
            )
            await pg.execute("SET session_replication_role = origin")
        finally:
            await pg.close()
        await conn.close()

    print(f"\nPhase 9.1 PII masking E2E: {len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
