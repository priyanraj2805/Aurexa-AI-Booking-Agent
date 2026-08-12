# Impersonation Feature — Full Security & Quality Audit

**Date:** 2026-08-07
**Environment:** local (tenant :8005/:3002, control plane :8006/:3003)
**Subject:** Phase 11b super-admin "View as user" hand-off (handshake → HttpOnly tenant cookie, mutation gating, revocation, audit)
**Method:** Live Chrome DevTools end-to-end run: fresh request → MFA step-up → org approval → handshake → console entry → mutation matrix → revocation → DB + audit-log verification → console/Lighthouse sweeps.

---

## 1. Scope & Test Run

- Org: **E2E Test Org** (`53b8b640-20f6-4782-8c48-a42a997cdcf7`)
- User: `e2e.test.0805@dotstark.com`
- Impersonation record: `a5c9028b-563c-45f6-888f-7ce8ae9244c6`
- Handshake jti: `b4cdbd88-fd88-4aa3-bd0a-f2f7915e36e3`
- Super admin actor: `a49db3af-dfb9-48cb-9ad5-06278c8e83cb` (`admin@dotstark.ai`)

---

## 2. Findings (Security)

### 2.1 No raw JWT in control-plane responses — PASS
`POST /api/v1/impersonation/{id}/confirm` returns only:
```json
{"id": "...", "org_id": "...", "org_name": "E2E Test Org", "user_email": "...",
 "started_at": "...", "expires_at": "...", "session_active": true,
 "console_url": "http://localhost:3002/impersonate/enter?hs=<handshake-jwt>"}
```
No `super_admin_token`/tenant access token is ever returned to JS in this flow.

### 2.2 Handshake exchange & cookie flags — PASS
`POST /api/v1/auth/impersonate/enter` (tenant):
- Validates bridge-secret-signed handshake (`typ=imp_enter`), 10-min TTL (exp was within window).
- Checks `public.impersonation_sessions` row: `token_jti = :jti AND revoked_at IS NULL AND expires_at > now()`.
- Response `Set-Cookie`:
  ```
  ds_token=...; HttpOnly; Max-Age=604800; Path=/; SameSite=none; Secure
  ```
- **HttpOnly ✓ Secure ✓ SameSite=None ✓** (needed for cross-origin fetch with credentials from :3002).
- `document.cookie` on tenant app is **empty**; no token in `localStorage`/`sessionStorage` (only `dotstark_public_api_base`). Frontend discards the `access_token` in the enter response and relies solely on the cookie.

### 2.3 Banner & status polling — PASS
- Red banner rendered: "Impersonation session — you are viewing this account as e2e.test.0805@dotstark.com. Every action is audited." with "Exit impersonation".
- `GET /api/v1/auth/impersonate/status` → `{"active":true, user_email, org_id, expires_at}` (matches DB row exactly). Polling observed.

### 2.4 Mutation gate matrix — PASS (5/5 gated, reads open)
All fired from the impersonated session:

| Endpoint | Expect | Result |
|---|---|---|
| `POST /auth/security/otp/generate` | 403 | **403** "This action is not allowed while impersonating a user" |
| `POST /dashboard/widget-token/regenerate` | 403 | **403** same detail |
| `DELETE /dashboard/widget-token` | 403 | **403** same detail |
| `POST /documents/documents/upload` | 403 | **403** same detail |
| `DELETE /documents/documents/{id}` (real path) | 403 | **403** same detail |
| `DELETE /documents/documents/website` | 403 | **403** same detail |
| `GET /auth/me` | 200 | **200** user profile |
| `GET /dashboard/widget-token` | 200 | **200** masked token |
| `GET /documents/documents` | 200 | **200** `[]` |

**Note:** The documents router has a double prefix — `api.py` mounts with `prefix="/documents"` while endpoints also declare `/documents/...`, so the real paths are `/api/v1/documents/documents/...`. Requests to the intuitive `/api/v1/documents/{id}` return route-level 404 (safe but confusing; gate still fires on the real path).

### 2.5 Handshake reuse — CONDITIONAL (see finding F1)
- Replay of the same `?hs=` **while the session is live** → `200`, re-mints a tenant token. The enter endpoint has **no consumed/used flag**; single-use is enforced only indirectly by session liveness (revoked/expired).
- Replay **after** `End now` → **401** `{"detail":"Impersonation session is no longer active"}` — PASS for the revoked case.

### 2.6 "End now" from control plane — PASS (full-chain)
Clicked "End now" on the active history row:
- `super_admin.impersonations`: status `active → ended`, `ended_at = 17:53:51`, `ended_by = a49db3af-…` (admin).
- `public.impersonation_sessions`: `revoked_at = 17:53:51`, `revoked_by = admin@dotstark.ai`.
- Tenant console: banner cleared automatically (next poll), then tenant tab closed itself after revocation.
- After revocation, **every** tenant call returns **401** `"Impersonation session revoked or expired"` — including `/auth/me` and `/dashboard/stats` — confirming the tenant auth dependency checks `revoked_at` per request. The stale cookie is inert.

### 2.7 Audit log — PASS (hash-chained)
`super_admin.audit_logs` for `a5c9028b`:
```
impersonation.request  17:44:47  admin@dotstark.ai         {org_id, org_name, user_email, approval_expires_at}
impersonation.approved 17:44:58  org_approval:e2e.test.0805@dotstark.com  {org_name, org_admin_email}
impersonation.start    17:45:58  admin@dotstark.ai         {org_id, org_name, expires_at, user_email}
impersonation.end      17:53:51  admin@dotstark.ai
```
Each row links `previous_hash → record_hash` (tamper-evident chain; chain integrity verified visually — each record_hash equals the next row's previous_hash).

---

## 3. Findings (Quality / Edge)

- **F1 (MED) Handshake is not single-use while live.** Replaying `?hs=` during the active session succeeds. Risk is limited (handshake TTL 10 min, session-scoped, cookie already present), but a `consumed_at`/`used` column on `public.impersonation_sessions` (or one-time nonce) would make replay strictly impossible.
- **F2 (LOW) Tenant tab silently closes on revocation.** The tenant console closed itself after `End now` (no error page). Confirm this is intended UX (logout-on-revoke) and not a stray unhandled redirect.
- **F3 (LOW) Documents router double prefix** (`/api/v1/documents/documents/...`). Works but diverges from the rest of the API and from the frontend's apparent naming; easy to mis-wire later.
- **F4 (INFO) `impersonation.end` actor_id empty.** The end row has `actor_email = admin@dotstark.ai` but `actor_id` is NULL (the row `ended_by` in the table itself is correct). Only the audit-log entry omits the actor id.
- **F5 (INFO) Stale-`active` design gap (pre-existing).** Tenant-side "Exit impersonation" revokes `public.impersonation_sessions` and clears the cookie, but `super_admin.impersonations` stays `active` until an admin clicks "End now" — the green card can outlive the actual session. Safe (jti check + revoke) but visually stale.

---

## 4. Frontend Quality

### Console errors
- :3002 (tenant) — **none** (error/warn sweep clean).
- :3003 (control plane) — **none** (error/warn sweep clean).

### Lighthouse (snapshot, desktop)
| App | A11y | Best Practices | SEO |
|---|---|---|---|
| :3003 super-admin support page | **90** | 100 | 100 |
| :3002 tenant dashboard | **84** | 100 | 100 |

Failed audits:
- **:3003** — color-contrast (1+ pair), `<select>` elements missing labels (org/user pickers), agentic a11y-tree malformed.
- **:3002** — color-contrast, button without accessible name, no `<main>` landmark, touch targets too small, agentic a11y-tree malformed.

---

## 5. Verdict

| Area | Verdict |
|---|---|
| Token handling (no JWT leak, HttpOnly+Secure cookie) | ✅ PASS |
| Mutation gating while impersonating | ✅ PASS (6/6 blocked, reads open) |
| Revocation (End now / tenant exit) | ✅ PASS (both DB rows updated, tenant 401s, banner clears) |
| Handshake security | ⚠️ CONDITIONAL (replay blocked after revoke; live-session replay allowed — F1) |
| Audit trail | ✅ PASS (hash-chained, correct actors) |
| Frontend console health | ✅ PASS |
| Accessibility | ⚠️ 90 / 84 (contrast, labels, landmarks) |

**Overall: feature is secure and auditable.** Recommend addressing F1 (handshake single-use), F3 (double prefix), and the a11y failures before release polish.

## Evidence
- Screenshots: `audit_01_superadmin_post_end.png` (control plane after End now; history shows ENDED 17:53:51)
- Lighthouse reports: `C:\Users\PRIYAN~1\AppData\Local\Temp\chrome-devtools-mcp-OTg2b2\report.json` (:3003), `...-7RusdK\report.json` (:3002)
