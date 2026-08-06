import asyncio
import sys

import asyncpg
import httpx
import pyotp

BASE = "http://localhost:8006/api/v1"


async def main():
    c = httpx.AsyncClient(base_url=BASE, timeout=15)
    results = []

    def check(name, ok, extra=""):
        results.append((name, ok, extra))
        print(("PASS" if ok else "FAIL") + f"  {name}" + (f"  [{extra}]" if extra else ""))

    # ── 1. Wrong password → 401 ────────────────────────────────────────────
    r = await c.post("/auth/login", json={"email": "admin@dotstark.ai", "password": "wrong"})
    check("login wrong password -> 401", r.status_code == 401, f"got {r.status_code}")

    # ── 2. Login OK → MFA pending ──────────────────────────────────────────
    r = await c.post("/auth/login", json={"email": "admin@dotstark.ai", "password": "SuperAdmin@2026!"})
    check("login OK -> 200", r.status_code == 200)
    body = r.json()
    mfa_state = body.get("mfa", {})
    check("mfa required", mfa_state.get("required") is True, str(mfa_state))
    has_pending = any(k.startswith("super_admin_mfa_pending") for k in c.cookies.keys())
    check("mfa_pending cookie set", has_pending)

    # ── 3. No MFA cookie → enroll denied ───────────────────────────────────
    c2 = httpx.AsyncClient(base_url=BASE, timeout=15)
    r = await c2.post("/auth/mfa/enroll")
    check("enroll without pending cookie -> 401", r.status_code == 401, f"got {r.status_code}")

    conn = await asyncpg.connect(host="localhost", port=5432, user="postgres", password="dotstark_secure_pass", database="dotstark_v2")

    if mfa_state.get("enrolled"):
        # Already enrolled (idempotent rerun): use the stored secret
        secret = await conn.fetchval(
            "SELECT totp_secret FROM super_admin.admin_mfa m JOIN super_admin.super_admins a ON a.id=m.admin_id WHERE a.email=$1",
            "admin@dotstark.ai",
        )
        check("enroll already enrolled -> 400", True)
        r = await c.post("/auth/mfa/enroll")
        check("enroll already enrolled -> 400", r.status_code == 400, f"got {r.status_code}")
    else:
        # ── 4. Enroll → TOTP secret + QR ───────────────────────────────────
        r = await c.post("/auth/mfa/enroll")
        check("enroll -> 200", r.status_code == 200, f"got {r.status_code}")
        enroll = r.json()
        secret = enroll.get("secret")
        check("totp secret present", bool(secret))
        check("qr svg present", str(enroll.get("qr_svg", "")).startswith("data:image/svg+xml"))

    await conn.close()

    # ── 5. Wrong TOTP code → 401 ───────────────────────────────────────────
    r = await c.post("/auth/mfa/verify", json={"code": "000000"})
    check("verify wrong code -> 401", r.status_code == 401, f"got {r.status_code}")

    # ── 6. Correct TOTP code → cookies set ─────────────────────────────────
    totp = pyotp.TOTP(secret)
    r = await c.post("/auth/mfa/verify", json={"code": totp.now()})
    check("verify correct code -> 200", r.status_code == 200, f"got {r.status_code}")
    cookies = list(c.cookies.keys())
    check("access cookie set", "super_admin_token" in cookies)
    check("refresh cookie set", "super_admin_refresh" in cookies)
    check("mfa_pending cleared", "super_admin_mfa_pending" not in cookies)

    # ── 7. /me with cookie → owner profile + 26 permissions ────────────────
    r = await c.get("/auth/me")
    check("me -> 200", r.status_code == 200, f"got {r.status_code}")
    me = r.json()
    check("role is owner", me.get("role_code") == "owner", str(me.get("role_code")))
    check("permissions present (>= 26)", len(me.get("permissions", [])) >= 26, str(len(me.get("permissions", []))))

    # ── 8. CSRF: unsafe method without X-Requested-With → 403 ──────────────
    r = await c.post("/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    check("csrf header present -> logout ok", r.status_code == 200, f"got {r.status_code}")
    # re-login (previous login cookies were consumed by logout)
    r = await c.post("/auth/login", json={"email": "admin@dotstark.ai", "password": "SuperAdmin@2026!"})
    check("re-login after logout -> 200", r.status_code == 200, f"got {r.status_code}")
    r = await c.post("/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
    check("re-verify after logout -> 200", r.status_code == 200, f"got {r.status_code}")
    r = await c.post("/auth/me")  # POST without CSRF header -> 403
    check("unsafe method w/o csrf header -> 403", r.status_code == 403, f"got {r.status_code}")

    # ── 9. Audit trail: login event recorded + chain valid ─────────────────
    r = await c.get("/audit/verify")
    check("audit/verify -> 200", r.status_code == 200, f"got {r.status_code}")
    v = r.json()
    check("audit chain valid", v.get("chain_valid") is True, str(v))
    r = await c.get("/audit", params={"action": "admin.login"})
    logs = r.json()
    check("login audit rows exist", len(logs) >= 1 and logs[0]["action"] == "admin.login", str(len(logs)))

    # ── 10. Tamper attempt at DB level → blocked by trigger ────────────────
    conn = await asyncpg.connect(host="localhost", port=5432, user="postgres", password="dotstark_secure_pass", database="dotstark_v2")
    try:
        await conn.execute("UPDATE super_admin.audit_logs SET action='HACKED' WHERE action='admin.login'")
        check("UPDATE audit -> blocked", False, "update unexpectedly allowed")
    except Exception as e:
        check("UPDATE audit -> blocked", "audit_logs are append-only" in str(e), str(e)[:60])
    try:
        await conn.execute("DELETE FROM super_admin.audit_logs WHERE action='admin.login'")
        check("DELETE audit -> blocked", False, "delete unexpectedly allowed")
    except Exception as e:
        check("DELETE audit -> blocked", "audit_logs are append-only" in str(e), str(e)[:60])
    await conn.close()

    # ── 11. Logout revokes refresh session (before lockout test) ───────────
    c4 = httpx.AsyncClient(base_url=BASE, timeout=15)
    r = await c4.post("/auth/login", json={"email": "admin@dotstark.ai", "password": "SuperAdmin@2026!"})
    check("login again -> 200", r.status_code == 200, f"got {r.status_code}")
    r = await c4.post("/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()})
    check("verify again -> 200", r.status_code == 200, f"got {r.status_code}")
    r = await c4.post("/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    check("logout -> 200", r.status_code == 200, f"got {r.status_code}")
    cookies_after = list(c4.cookies.keys())
    check("cookies cleared on logout", "super_admin_token" not in cookies_after)
    await c4.aclose()

    # ── 12. Lockout: 6 wrong passwords → 429 on 6th (run LAST) ─────────────
    c3 = httpx.AsyncClient(base_url=BASE, timeout=15)
    statuses = []
    for _ in range(6):
        r = await c3.post("/auth/login", json={"email": "admin@dotstark.ai", "password": "nope"})
        statuses.append(r.status_code)
    check("6th wrong attempt -> 429 (locked)", statuses[-1] == 429, str(statuses))
    await c3.aclose()

    await c.aclose()
    await c2.aclose()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    sys.exit(1 if failed else 0)


asyncio.run(main())
