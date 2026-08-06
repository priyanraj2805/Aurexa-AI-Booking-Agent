"""Phase 8 E2E (backend): support & security — impersonation (MFA step-up,
30-min token, single active session, revocation), conversation peek with PII
masking, security policy (password policy, IP allowlist), step-up on
destructive actions, change-password, audit rows.

Cleanup: removes E2E orgs/sessions/conversations and test admins.
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
SUPPORT_EMAIL = "p8.support@dotstark.ai"
SUPPORT_PASSWORD = "Support@2026!"
OPS_EMAIL = "p8.ops@dotstark.ai"
OPS_PASSWORD = "Ops@2026!"
AUDITOR_EMAIL = "p8.auditor@dotstark.ai"
AUDITOR_PASSWORD = "Auditor@2026!"
OWNER2_EMAIL = "p8.owner2@dotstark.ai"
OWNER2_PASSWORD = "Owner2@2026!"
PW_EMAIL = "p8.pwchange@dotstark.ai"
PW_PASSWORD = "PwChange@2026!"


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


async def reset_admin(conn, email, password, role_code, status="active"):
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
            "UPDATE super_admin.super_admins SET password_hash = $1, status = $3 "
            "WHERE id = $2",
            pwd_ctx.hash(password),
            row["id"],
            status,
        )
    else:
        await conn.execute(
            """
            INSERT INTO super_admin.super_admins
                (id, email, password_hash, full_name, role_id, status, mfa_required)
            SELECT gen_random_uuid(), $1, $2, 'P8 E2E Admin', id, $4, TRUE
            FROM super_admin.roles WHERE code = $3
            """,
            email, pwd_ctx.hash(password), role_code, status,
        )


async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:dotstark_secure_pass@localhost:5432/dotstark_v2"
    )
    CSRF = {"X-Requested-With": "XMLHttpRequest"}

    for email, password, role in (
        (SUPPORT_EMAIL, SUPPORT_PASSWORD, "support"),
        (OPS_EMAIL, OPS_PASSWORD, "operations"),
        (AUDITOR_EMAIL, AUDITOR_PASSWORD, "auditor"),
        (OWNER2_EMAIL, OWNER2_PASSWORD, "owner"),
        (PW_EMAIL, PW_PASSWORD, "support"),
    ):
        await reset_admin(conn, email, password, role)

    # Cleanup previous E2E state
    await conn.execute(
        """
        UPDATE super_admin.security_settings
        SET ip_allowlist_enabled = FALSE, ip_allowlist_cidrs = '[]',
            mfa_enforced = TRUE, password_min_length = 8,
            password_require_upper = TRUE, password_require_digit = TRUE,
            password_require_special = TRUE
        WHERE id = 1
        """
    )
    await conn.execute(
        """
        DELETE FROM super_admin.impersonations WHERE user_email LIKE 'e2e.p8%'
           OR user_email LIKE '%p8-support%'
        """
    )
    await conn.execute(
        "DELETE FROM public.impersonation_sessions WHERE user_email LIKE '%@test.com' "
        "AND user_email LIKE 'p8%'"
    )
    await conn.execute(
        "DELETE FROM public.chat_messages WHERE session_id IN "
        "(SELECT id FROM public.chat_sessions WHERE org_id IN "
        "(SELECT id FROM public.organizations WHERE name LIKE 'E2E Phase8%'))"
    )
    await conn.execute(
        "DELETE FROM public.chat_sessions WHERE org_id IN "
        "(SELECT id FROM public.organizations WHERE name LIKE 'E2E Phase8%')"
    )
    await conn.execute("DELETE FROM public.organizations WHERE name LIKE 'E2E Phase8%'")

    owner_secret = await conn.fetchval(
        "SELECT totp_secret FROM super_admin.admin_mfa WHERE admin_id = "
        "(SELECT id FROM super_admin.super_admins WHERE email = $1)",
        OWNER_EMAIL,
    )

    # Target org + tenant user for impersonation + peek
    target = await conn.fetchrow(
        """
        SELECT u.email, u.id, u.org_id, o.name AS org_name
        FROM public.users u JOIN public.organizations o ON o.id = u.org_id
        WHERE u.is_active = TRUE AND o.is_active = TRUE
        ORDER BY u.created_at ASC LIMIT 1
        """
    )
    target_email, target_org = target["email"], str(target["org_id"])
    target_user_id = str(target["id"])

    # Disposable org for the org-delete approval test
    delete_org_id = str(
        await conn.fetchval(
            """
            INSERT INTO public.organizations (id, name, api_key, is_active)
            VALUES (gen_random_uuid(), 'E2E Phase8 Delete Org',
                    'e2e-phase8-' || gen_random_uuid()::text, TRUE)
            RETURNING id
            """
        )
    )

    # Seed a conversation with PII for the peek test
    session_id = str(
        await conn.fetchval(
            """
            INSERT INTO public.chat_sessions
                (id, org_id, channel, channel_user_id, state_data, session_status,
                 is_active, otp_verified)
            VALUES (gen_random_uuid(), $1, 'whatsapp', 'wa_919876543210',
                    '{"name": "John Smith", "email": "john.smith@example.com",
                      "phone": "+919876543210"}',
                    'active', TRUE, FALSE)
            RETURNING id
            """,
            target_org,
        )
    )
    await conn.execute(
        """
        INSERT INTO public.chat_messages (id, session_id, role, content, sender_type)
        VALUES
            (gen_random_uuid(), $1, 'user', 'Hi, my email is john.smith@example.com and my phone is +1 555-123-4567', 'user'),
            (gen_random_uuid(), $1, 'agent', 'Thanks John! We will call you at 555-123-4567 shortly.', 'ai')
        """,
        session_id,
    )

    async with httpx.AsyncClient(base_url=SUPER, headers=CSRF) as owner, \
        httpx.AsyncClient(base_url=SUPER, headers=CSRF) as support, \
        httpx.AsyncClient(base_url=SUPER, headers=CSRF) as ops, \
        httpx.AsyncClient(base_url=SUPER, headers=CSRF) as auditor, \
        httpx.AsyncClient(base_url=SUPER, headers=CSRF) as owner2, \
        httpx.AsyncClient(base_url=SUPER, headers=CSRF) as pw_client, \
        httpx.AsyncClient(base_url=TENANT) as tenant:
        await login_mfa(owner, OWNER_EMAIL, OWNER_PASSWORD, owner_secret)
        support_secret = await enroll_login(support, SUPPORT_EMAIL, SUPPORT_PASSWORD)
        await enroll_login(ops, OPS_EMAIL, OPS_PASSWORD)
        await enroll_login(auditor, AUDITOR_EMAIL, AUDITOR_PASSWORD)
        owner2_secret = await enroll_login(owner2, OWNER2_EMAIL, OWNER2_PASSWORD)
        await enroll_login(pw_client, PW_EMAIL, PW_PASSWORD)

        # ============ 1. Security settings ============
        r = await owner.get("/api/v1/security/settings")
        check("security settings GET 200", r.status_code == 200 and r.json()["mfa_enforced"] is True, r.text[:200])
        r = await ops.get("/api/v1/security/settings")
        check("ops security settings 403", r.status_code == 403, r.text)

        new_settings = {
            "mfa_enforced": True,
            "password_policy": {"min_length": 10, "require_upper": True, "require_digit": True, "require_special": True},
            "ip_allowlist": {"enabled": False, "cidrs": []},
        }
        r = await owner.put("/api/v1/security/settings", json=new_settings)
        check("security settings PUT 200", r.status_code == 200 and r.json()["password_policy"]["min_length"] == 10, r.text[:300])
        r = await owner.put("/api/v1/security/settings", json={
            **new_settings, "ip_allowlist": {"enabled": True, "cidrs": ["banana/33"]}})
        check("invalid CIDR 422", r.status_code == 422, r.text)

        # ============ 2. Password policy + change-password ============
        r = await pw_client.post("/api/v1/auth/change-password", json={
            "current_password": PW_PASSWORD, "new_password": "short1!"})
        check("change-password weak 422", r.status_code == 422, r.text)
        r = await pw_client.post("/api/v1/auth/change-password", json={
            "current_password": PW_PASSWORD, "new_password": "Str0ng!New2026"})
        check("change-password strong 200", r.status_code == 200, r.text[:200])
        r = await pw_client.post("/api/v1/auth/change-password", json={
            "current_password": "wrong-old", "new_password": "Str0ng!New2026"})
        check("change-password wrong old 403", r.status_code == 403, r.text)

        # ============ 3. IP allowlist enforcement ============
        r = await owner.put("/api/v1/security/settings", json={
            **new_settings, "ip_allowlist": {"enabled": True, "cidrs": ["127.0.0.1/32"]}})
        check("allowlist loopback on", r.status_code == 200, r.text[:200])
        r = await owner.get("/api/v1/audit")
        check("loopback still allowed", r.status_code == 200, r.text[:200])
        r = await owner.put("/api/v1/security/settings", json={
            **new_settings, "ip_allowlist": {"enabled": True, "cidrs": ["10.0.0.0/8"]}})
        check("allowlist moved to 10/8", r.status_code == 200, r.text[:200])
        r = await owner.get("/api/v1/audit")
        check("10.x IP blocked 403", r.status_code == 403, r.text[:200])
        spoof = httpx.AsyncClient(base_url=SUPER, headers=CSRF, cookies=owner.cookies)
        r = await spoof.get("/api/v1/audit", headers={"X-Forwarded-For": "10.0.0.7"})
        check("x-forwarded-for 10.x allowed", r.status_code == 200, r.text[:200])
        r = await spoof.get("/api/v1/audit", headers={"X-Forwarded-For": "127.0.0.1"})
        check("x-forwarded-for loopback still blocked", r.status_code == 403, r.text[:200])
        r = await spoof.put("/api/v1/security/settings", headers={"X-Forwarded-For": "10.0.0.7"}, json={
            **new_settings, "ip_allowlist": {"enabled": False, "cidrs": []}})
        check("allowlist off via allowed XFF", r.status_code == 200, r.text[:200])
        await spoof.aclose()
        r = await owner.get("/api/v1/audit")
        check("audit works after allowlist off", r.status_code == 200, r.text[:200])

        # ============ 4. Impersonation ============
        r = await owner.post("/api/v1/impersonation/start", json={
            "org_id": target_org, "user_email": target_email})
        check("impersonate without stepup 403", r.status_code == 403, r.text[:200])

        r = await owner.post("/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(owner_secret).now()})
        check("mfa stepup 200", r.status_code == 200, r.text[:200])

        r = await owner.post("/api/v1/impersonation/start", json={
            "org_id": target_org, "user_email": target_email})
        check("impersonate start 200", r.status_code == 200, r.text[:300])
        imp1 = r.json()
        check("impersonation returns token", bool(imp1.get("token")) and "expires_at" in imp1, r.text[:300])
        check("impersonation returns masked? no: full email", imp1.get("user_email") == target_email, r.text[:300])

        r = await owner.get("/api/v1/impersonation/active")
        check("active impersonation", r.status_code == 200 and r.json() and r.json()["id"] == imp1["id"], r.text[:300])

        r = await owner.post("/api/v1/impersonation/start", json={
            "org_id": target_org, "user_email": target_email})
        check("second start 200 (single session)", r.status_code == 200, r.text[:300])
        imp2 = r.json()
        check("imp2 id differs", imp2["id"] != imp1["id"])
        r = await owner.get("/api/v1/impersonation")
        statuses = {i["id"]: i["status"] for i in r.json()}
        check("first session superseded", statuses.get(imp1["id"]) == "ended" and statuses.get(imp2["id"]) == "active", r.text[:300])

        # Tenant-side token usability
        r = await tenant.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {imp2['token']}"})
        check("tenant me as impersonated admin 200", r.status_code == 200, r.text[:300])
        body = r.json()
        check("tenant identity is target", body.get("email") == target_email, r.text[:300])
        check("tenant org matches target", str(body.get("org_id", "")) == target_org, r.text[:300])

        # Stop -> token must die
        r = await owner.post(f"/api/v1/impersonation/{imp2['id']}/stop")
        check("impersonation stop 200", r.status_code == 200, r.text[:200])
        r = await tenant.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {imp2['token']}"})
        check("tenant me after stop 401", r.status_code == 401, r.text[:200])

        r = await support.post("/api/v1/impersonation/start", json={
            "org_id": target_org, "user_email": target_email})
        check("support impersonate without stepup 403", r.status_code == 403, r.text[:200])
        r = await support.post("/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(support_secret).now()})
        check("support stepup 200", r.status_code == 200, r.text[:200])
        r = await support.post("/api/v1/impersonation/start", json={
            "org_id": target_org, "user_email": target_email})
        check("support impersonate start 200", r.status_code == 200, r.text[:300])
        imp3 = r.json()
        r = await support.post(f"/api/v1/impersonation/{imp3['id']}/stop")
        check("support stop 200", r.status_code == 200, r.text[:200])
        r = await ops.post("/api/v1/impersonation/start", json={
            "org_id": target_org, "user_email": target_email})
        check("ops impersonate 403", r.status_code == 403, r.text[:200])

        # ============ 5. Conversation peek + PII masking ============
        r = await auditor.get(f"/api/v1/support/orgs/{target_org}/conversations")
        check("auditor peek 403", r.status_code == 403, r.text[:200])
        r = await support.get(f"/api/v1/support/orgs/{target_org}/conversations")
        check("support conversations list 200", r.status_code == 200, r.text[:300])
        items = r.json()["items"]
        check("seeded conversation listed", any(i["id"] == session_id for i in items), r.text[:300])
        seeded = next(i for i in items if i["id"] == session_id)
        check("channel_user_id masked", seeded["channel_user_id"] is not None and "9876543210" not in seeded["channel_user_id"], seeded["channel_user_id"])
        check("channel_user_id has no wa_ prefix leak", not seeded["channel_user_id"].startswith("wa_"), seeded["channel_user_id"])

        r = await support.get(f"/api/v1/support/orgs/{target_org}/conversations/{session_id}/messages")
        check("peek transcript 200", r.status_code == 200, r.text[:400])
        peek = r.json()
        check("peek state name masked", "John" not in str(peek["state_data"]), str(peek["state_data"]))
        check("peek state email masked", "john.smith@example.com" not in str(peek["state_data"]), str(peek["state_data"]))
        content = " ".join(m["content"] for m in peek["messages"])
        check("email masked in content", "john.smith@example.com" not in content and "@example.com" not in content, content[:200])
        check("phone masked in content", "555-123-4567" not in content and "9876543210" not in content, content[:200])
        check("peek returns messages", len(peek["messages"]) == 2, r.text[:300])
        r = await ops.get(f"/api/v1/support/orgs/{target_org}/conversations/{session_id}/messages")
        check("ops peek 200 (granted)", r.status_code == 200, r.text[:200])

        # ============ 6. Step-up on org delete approval ============
        r = await owner.post("/api/v1/approvals", json={
            "request_type": "org.delete", "resource_id": delete_org_id,
            "payload": {"reason": "E2E phase8 delete"}})
        check("file org.delete approval", r.status_code == 200, r.text[:300])
        approval_id = r.json()["approval_id"]
        r = await owner2.post(f"/api/v1/approvals/{approval_id}/approve", json={"note": "approve"})
        check("approve org.delete without stepup 403", r.status_code == 403, r.text[:300])
        r = await owner2.post("/api/v1/auth/mfa/stepup", json={"code": pyotp.TOTP(owner2_secret).now()})
        check("owner2 stepup 200", r.status_code == 200, r.text[:200])
        r = await owner2.post(f"/api/v1/approvals/{approval_id}/approve", json={"note": "approve"})
        check("approve org.delete with stepup executes", r.status_code == 200, r.text[:300])
        still = await conn.fetchval(
            "SELECT is_active FROM public.organizations WHERE id = $1", delete_org_id
        )
        check("org deleted by bridge", still is None, str(still))

        # ============ 7. Audit ============
        r = await owner.get("/api/v1/audit?limit=200")
        actions = [a["action"] for a in r.json()]
        for wanted in (
            "security.settings.update", "admin.password_change",
            "impersonation.start", "impersonation.end",
            "support.conversation.peek",
        ):
            check(f"audit {wanted}", wanted in actions)

        # ---- Cleanup ----
        await conn.execute("DELETE FROM super_admin.impersonations WHERE admin_id IN "
                           "(SELECT id FROM super_admin.super_admins WHERE email LIKE 'p8.%')")
        await conn.execute(
            "DELETE FROM public.impersonation_sessions WHERE user_email = $1",
            target_email,
        )
        await conn.execute("DELETE FROM public.chat_messages WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM public.chat_sessions WHERE id = $1", session_id)
        await conn.execute("DELETE FROM public.organizations WHERE id = $1", delete_org_id)
        for email in (SUPPORT_EMAIL, OPS_EMAIL, AUDITOR_EMAIL, OWNER2_EMAIL, PW_EMAIL):
            row = await conn.fetchrow(
                "SELECT id FROM super_admin.super_admins WHERE email = $1", email
            )
            await conn.execute("DELETE FROM super_admin.admin_sessions WHERE admin_id = $1", row["id"])
            await conn.execute("DELETE FROM super_admin.admin_mfa WHERE admin_id = $1", row["id"])
            await conn.execute(
                "UPDATE super_admin.super_admins SET status = 'disabled' WHERE id = $1",
                row["id"],
            )

    print(f"\nE2E phase 8 support & security: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


asyncio.run(main())
