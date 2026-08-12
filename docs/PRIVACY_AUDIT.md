# Sensitive Data Exposure / Privacy Audit Report

**Application:** DotStark AI Booking Assistant (multi-tenant SaaS)
**Date:** 2026-08-10
**Scope:** Organization Admin Panel (:3002), Customer Chat Widget, Super Admin Panel (:3003), tenant backend (:8005), control plane (:8006), shared PostgreSQL (`dotstark_v2`)
**Mode:** AUDIT ONLY — no code, data, configuration, or credentials were modified.

---

## EXECUTIVE SUMMARY

The platform has strong *tenant isolation* (verified live — cross-org access returns 404) and a clean *client-side token handling* model (HttpOnly cookies, no JWTs in localStorage). However, the **running configuration is weaponizable**: three secrets ship with published, unrotated default values, and every one was **forged and accepted live** during this audit:

1. **Super Admin JWT secret** — forged a super-admin token with the default secret and authenticated to the control plane as a real admin (`200 OK` on `/auth/me`).
2. **Internal bridge secret** — forged a valid HMAC signature with the default secret; the tenant backend accepted it (request passed auth, only failed body validation).
3. **Firecrawl webhook** — completely unauthenticated; accepts *any* `org_id` from the query string and can inject arbitrary content into any organization's knowledge base (prompt-injection / KB-poisoning / DoS).

These are supplemented by PII exposure in audit logs to low-privileged roles, secrets in URLs and logs, and a handful of medium/low hardening gaps.

### Overall Sensitive Data Exposure Score: **52 / 100**

| Severity | Count |
|---|---|
| Critical | 3 |
| High | 6 |
| Medium | 9 |
| Low | 4 |
| Informational | 6 |

---

## 1. CRITICAL FINDINGS

---

### Finding C1 — Super Admin JWT can be forged (default secret active in the running system)

- **Finding:** The control plane signs all admin JWTs with `SUPER_ADMIN_JWT_SECRET_KEY`, **which defaults to `dev-super-admin-secret-2026-change-in-prod`** and is **not overridden in `backend-super/.env`** (verified — the key is absent from the file).
- **Severity:** Critical
- **Affected Component:** `backend-super` — all `/api/v1/*` endpoints (`app/core/security.py:36`, default in `app/core/config.py:32-35`).
- **Affected User:** Anyone with network access to :8006 (or the public tunnel) — no login needed.
- **Exposed Data:** Full control-plane capability: every tenant's users, bookings, conversations, support tickets, audit logs, billing/invoices, feature flags, and impersonation as any user.
- **Where Exposed:** HS256-signed JWT; forgeable offline with the known default.
- **How It Was Discovered:** Wrote a 20-line script using the app's own `jose` library, signed a token with the default secret, and called `GET /api/v1/auth/me` with it.
- **Evidence:** `FORGED_ME: 200 {"id":"3e7e7389-...","email":"p9.pii.105dd15a@dotstark.ai","permissions":["dashboard.view",...,"support.impersonate","audit.view",...]}` — an attacker-issued token authenticated as a real, active admin.
- **Expected Behavior:** JWT must be signed with a strong, unique, unguessable secret configured only at deployment time.
- **Actual Behavior:** Default secret from source code is live; tokens signed with it are accepted.
- **Security Impact:** Total compromise of the super-admin plane (credential-less). An attacker forging an `owner`-role token can read all tenants' PII, refund invoices, disable orgs, and impersonate any user.
- **Privacy Impact:** All customers' PII across all tenants exposed.
- **Recommended Fix:** Generate a strong random secret, set `SUPER_ADMIN_JWT_SECRET_KEY` in `.env`, and make the control plane refuse to start (or refuse to authenticate) when the secret equals a known default.
- **Regression Risk:** Low — requires a coordinated `.env` change and logout of existing sessions (tokens re-signed).

---

### Finding C2 — Internal bridge HMAC accepted with default secret (arbitrary tenant lifecycle writes)

- **Finding:** The tenant backend's internal bridge is **enabled** (`INTERNAL_BRIDGE_ENABLED=true`) and verifies signatures against `INTERNAL_BRIDGE_SECRET`, which is set to the published default `dev_bridge_secret_2026` in `backend/.env` (and hard-coded as a default in config).
- **Severity:** Critical
- **Affected Component:** `backend/app/api/v1/internal/bridge.py` — all bridge routes (`/orgs`, `/orgs/{id}/suspend|restore|delete`, `/users/{id}/disable|enable`, `/impersonate/start|stop`, `/orgs/{id}/config`).
- **Affected User:** Anyone with network access; no tenant credentials needed.
- **Exposed Data:** Create/suspend/restore/delete **any** org, disable/enable **any** user, start/stop impersonation as **any** user in **any** org, read vulnerability details, modify org config.
- **Where Exposed:** HMAC-SHA256 over `timestamp.method.path.admin_email.sha256(body)` with the shared default secret; signature bytes are shipped in `X-Bridge-Signature` headers.
- **How It Was Discovered:** Reconstructed the canonical signing payload, signed it with `dev_bridge_secret_2026`, and POSTed to `/api/v1/internal/bridge/orgs`.
- **Evidence:** `BRIDGE_FORGED: HTTP 422 {"detail":[{"type":"missing","loc":["body","name"],...}]}` — **422 is body validation**, which means the forged signature **passed** `verify_bridge` (a 401 would have meant rejection). The bridge also requires no second factor; an attacker with this signature can call every bridge endpoint.
- **Expected Behavior:** Bridge secret must be unique per deployment, and the bridge should be disabled outside dedicated internal networks.
- **Actual Behavior:** Default secret accepted; bridge enabled.
- **Security Impact:** Complete tenant-platform compromise — org deletion, user disable, impersonation — bypassing the control-plane RBAC and step-up MFA entirely.
- **Privacy Impact:** Attacker gains read-access to any org's data by impersonating real users.
- **Recommended Fix:** Rotate `INTERNAL_BRIDGE_SECRET` immediately; enforce the existing `DEV_BRIDGE_SECRET` guard (`bridge_client.py:25-34`) on the *verifying* side too (tenant `bridge.py` never checks for the dev value); keep the bridge bind-loopback-only or off by default.
- **Regression Risk:** Low (config only); guard must be careful not to break legit control-plane calls.

---

### Finding C3 — Unauthenticated Firecrawl webhook can poison ANY org's knowledge base

- **Finding:** `POST /api/v1/firecrawl/webhook?org_id=<uuid>` has **no authentication, no signature check, no crawl-ownership check, and no rate limit**. The `org_id` comes straight from the query string and arbitrary `markdown` text is chunked and inserted into `document_chunks` for that org.
- **Severity:** Critical
- **Affected Component:** `backend/app/api/v1/endpoints/firecrawl_webhook.py:52-134` → `services/retrieval_service.py:150-205`.
- **Affected User:** Anyone with the URL — e.g. any visitor of a public widget page, or any internet client.
- **Exposed Data:** Injected text lands in the victim org's RAG corpus **and** is appended to the chat agent's system prompt on every knowledge query (`graph.py:318-324`) — enabling prompt injection against the victim's customers AND data exfiltration attempts.
- **Where Exposed:** Public `POST /api/v1/firecrawl/webhook`.
- **How It Was Discovered:** Called the endpoint with a bogus `org_id` and an empty payload.
- **Evidence:** `FIREWALL TEST → STATUS: 200 {"success":true}` with **no credentials** and `org_id=00000000-...` (a nonexistent org) — the endpoint is fully public. The handler never validates that `crawl_id` belongs to the org, nor that a crawl was started.
- **Expected Behavior:** Webhook must verify a shared secret (e.g. Firecrawl signing + `crawl_id` mapping to an org-scoped crawl record) and must bound ingestion volume per org.
- **Actual Behavior:** Unauthenticated, unbounded, arbitrary-org writes.
- **Security Impact:** Knowledge-base poisoning, LLM prompt injection against the victim org's customers (booking data exfiltration potential), and storage DoS via unlimited chunk flooding.
- **Privacy Impact:** Indirect — poisoned KB lets the LLM be manipulated to reveal or mis-handle customer data.
- **Recommended Fix:** Require Firecrawl webhook signature verification (shared secret header) and cross-check `crawl_id` against a previously-created crawl owned by that org; add per-org ingestion caps and rate limits.
- **Regression Risk:** Low — Firecrawl sends the same payloads; only admission checks are added.

---

## 2. HIGH FINDINGS

---

### Finding H1 — Impersonation handshake / approval tokens travel in URLs and logs

- **Finding:** `console_url?...hs=<handshake JWT>` is returned in API responses and opened directly in the browser URL bar; impersonation **approval links** (`?token=<32-byte secret>`) are public GET endpoints and, when SMTP is unset, are logged in cleartext via `logger.warning` and echoed in the API response.
- **Severity:** High
- **Affected Component:** `backend-super/app/core/impersonation_notify.py:33-41`; `impersonation.py:274-291` (public approve/reject) and `:377,475-478` (handshake in URL) — audited and partially fixed in a prior pass (handshake is now single-use).
- **Affected User:** Super admins, org admins being impersonated; anyone who can read server logs (ops staff, log aggregator compromise, shared logging platforms).
- **Exposed Data:** One-time capability tokens that (a) approve/reject impersonation of a specific org user, or (b) open a tenant session as the impersonated user.
- **Where Exposed:** Referer headers, server/access logs, browser history, proxy logs — and the control plane's own application log when SMTP is unset (confirmed: `backend-super/.env` has no SMTP config).
- **How It Was Discovered:** Code audit of `impersonation_notify.py` + reading the `.env` (no `SMTP_HOST`), plus prior handshake-replay testing.
- **Expected Behavior:** Tokens should be short-lived, single-use (approval tokens are), and should never be logged or sent in `Referer` (e.g. use POST-returned tokens entered into a form instead of URL deep links).
- **Actual Behavior:** Approval tokens appended to URLs and logged verbatim on the "fail-open" path; handshake still rides in URLs (mitigated by single-use expiry).
- **Security Impact:** Token theft → impersonation of org users or approval of pending impersonation requests.
- **Privacy Impact:** Attacker impersonates a real user of a real org and can read that org's data.
- **Recommended Fix:** Don't log full links (log ids only, not `token=` query parts); use `Cache-Control: no-store` on approve/reject responses; consider POST-with-token-in-body UX.
- **Regression Risk:** Low; SMTP is currently unset, so approval emails aren't sent anyway — the links are only recoverable via the response/log today.

---

### Finding H2 — Audit log PII readable by low-privileged roles (support / operations / auditor)

- **Finding:** `audit.view` is granted to **owner, super_admin, operations, support, and auditor** (`superadmin_phase0_foundations.py:79,83,87`), and `GET /api/v1/audit` + `GET /tenants/orgs/{org_id}/audit` return raw `old_values`/`new_values` JSON, `ip_address`, and `user_agent`. These payloads routinely embed customer email addresses (impersonation requests, support tickets), org names, refund reasons, and full security-settings dumps — **without** the `pii.unmask` gating applied to tenant user/bookings endpoints.
- **Severity:** High
- **Affected Component:** `backend-super/app/api/v1/endpoints/audit.py:36-49`, `tenants.py:556-598`; RBAC seeds.
- **Affected User:** Any support/operations/auditor admin who should *not* see unmasked customer PII (the established boundary is `pii.unmask`, owner/super-admin only).
- **Exposed Data:** Customer/admin emails, IPs, user agents, ticket subjects, refund reasons.
- **Where Exposed:** `/api/v1/audit`, `/api/v1/tenants/orgs/{org_id}/audit`, `/api/v1/tenants/users/{user_id}/activity`.
- **How It Was Discovered:** RBAC matrix audit + endpoint response-schema review; confirmed `support` role possesses `audit.view` via the forged-token `/auth/me` response.
- **Expected Behavior:** Emails/PII in audit `new_values` should be masked for roles lacking `pii.unmask` (or PII should be stored separately as an unmask-able column).
- **Actual Behavior:** Full PII in JSON for all five roles.
- **Security Impact:** Internal PII disclosure; insider-risk escalation; GDPR obligations affected.
- **Privacy Impact:** Customer emails/booking-related personal data exposed to support staff without authorization boundary.
- **Recommended Fix:** Apply `pii.unmask` checks to `actor_email`/`user_email` inside `new_values` on audit responses, or add a mask layer for non-unmask roles.
- **Regression Risk:** Medium — super-admin frontend audit page must still get unmasked values for the approved roles; masking must be server-side, not UI-only.

---

### Finding H3 — Legacy org API key accepted by public widget/booking endpoints (plaintext in DB)

- **Finding:** `GET /widget/config`, `/widget/history`, `/widget/availability`, `POST /chat/message/dispatch`, and parts of `slots.py` accept the org's **legacy `api_key`** (the full server-generated org key, stored **plaintext** in `organizations.api_key`) as an alternative to the scoped `widget_token`. If a tenant uses the legacy `data-api-key` embed, the full org key is embedded in their public website HTML.
- **Severity:** High
- **Affected Component:** `backend/app/api/v1/endpoints/widget.py:161-187`, `chat.py:45-63`, `slots.py:27-28`; `models/organization.py:12-24`; widget.js embed.
- **Affected User:** Any org's customers (via the public widget page) — the key is retrievable by anyone viewing page source when the legacy embed is used.
- **Exposed Data:** Org-identity credential with broader scope than the widget token (booking modification paths, full chat).
- **Where Exposed:** Public HTML, query strings on `/widget/*`.
- **How It Was Discovered:** Code audit of public endpoints + widget bundle; token-scope comparison.
- **Expected Behavior:** Only the scoped `widget_token` should be embeddable; org `api_key` should be server-only and never accepted by public embedding endpoints.
- **Actual Behavior:** Both accepted; legacy key exposes broader capability in page source.
- **Security Impact:** Credential theft → chat impersonation of the org, booking manipulation via slots endpoints, sustained access until rotation (keys rotate only on demand).
- **Privacy Impact:** Attacker-driven booking-side effects (not direct PII read, but session-level access to org data via chat).
- **Recommended Fix:** Stop accepting `api_key` on public endpoints (keep only for internal use); migrate tenants off the legacy embed; store keys hashed/encrypted.
- **Regression Risk:** Medium — any live tenant still on the legacy `data-api-key` snippet would break; must be coordinated with embed migration.

---

### Finding H4 — OAuth `state=org_id` without CSRF nonce + public org-UUID disclosure

- **Finding:** Google/Outlook OAuth callbacks associate tokens using `state = org_id` with no random nonce (`integrations.py:45`, `dashboard.py:2216-2217`), and the public widget WebSocket `connected` event discloses the org UUID (`widget_ws.py:100-106`). An attacker who knows an org UUID can start an OAuth flow with `state=<victim org>` and link their **own** Google/Outlook account to the victim org's calendar (account-linking confusion / provider takeover of booking links).
- **Severity:** High
- **Affected Component:** `backend/app/api/v1/endpoints/integrations.py:32-45`, `dashboard.py:2216+`, `widget_ws.py:100-106`.
- **Affected User:** Any org's admins (target), any site visitor (attacker).
- **Exposed Data:** No credentials leaked — but the linkage change ("which Google account owns this org's calendar") is attacker-directed.
- **Where Exposed:** OAuth callback parameters; public WS event.
- **How It Was Discovered:** Code audit + prior widget inspection.
- **Expected Behavior:** `state` must be a high-entropy nonce bound server-side to the initiating session/org (`_oauth_state_signature` uses a **hard-coded fallback** `"dotstark-oauth-state"` when the env secret is unset — `dashboard.py:217-223`).
- **Actual Behavior:** Static, predictable state; org UUID is public.
- **Security Impact:** Cross-tenant account-linking confusion; potential MITM of OAuth callback if signature fallback is used.
- **Privacy Impact:** Booking calendar integrity per tenant.
- **Recommended Fix:** Use random per-request `state` nonces persisted server-side; hard-fail if the state secret is unset; stop sending `org_id` in the public WS `connected` payload.
- **Regression Risk:** Low to medium (OAuth reauth needed).

---

### Finding H5 — MFA TOTP secret returned plaintext + no step-up rate limiting

- **Finding:** `POST /auth/mfa/enroll` returns the raw TOTP base secret and otpauth URL in the response body (`auth.py:174-201`, schema `MFAEnrollResponse`). Step-up verify (`/auth/mfa/stepup`, `/mfa/verify`) has **no per-account rate limit** (only `/login` is limited).
- **Severity:** High (Medium for enroll — standard UX — but High for the missing step-up brute-force protection on sensitive actions like refunds/impersonation)
- **Affected Component:** `backend-super/app/api/v1/endpoints/auth.py:161-320`; `app/core/stepup.py`.
- **Affected User:** Super admins; attacker targeting an admin.
- **Exposed Data:** TOTP seed (one-time at enrollment — expected); persistent brute-force surface for the 6-digit step-up code.
- **Where Exposed:** `/auth/mfa/enroll` response (JSON), `/auth/mfa/verify` + `/auth/mfa/stepup`.
- **How It Was Discovered:** Response-schema review.
- **Expected Behavior:** Step-up and verify endpoints should be rate-limited per admin + per IP (e.g. 5 attempts / 10 min, lockout).
- **Actual Behavior:** Unlimited attempts against a 6-digit TOTP with a 2-window validation — feasible brute-force in absence of rate limiting.
- **Security Impact:** Refund / impersonation actions protected only by TOTP + no throttle.
- **Privacy Impact:** n/a directly.
- **Recommended Fix:** Add `@limiter` or DB-backed attempt counters to `verify`/`stepup`; show the TOTP secret only once with `Cache-Control: no-store`.
- **Regression Risk:** Low.

---

### Finding H6 — Unauthenticated `/docs` + OpenAPI + `/health` disclose internal surface on BOTH backends

- **Finding:** `/docs` and `/api/v1/openapi.json` are publicly reachable on :8005 and :8006 (verified live `200`), and `GET /health` (also unauthenticated) discloses DB connectivity status, LLM/email/firecrawl/calendar dependency state, and bridge/telemetry gate flags.
- **Severity:** High (in combination with C1/C2 — the OpenAPI spec literally documents every exploitable route, and health reveals which subsystems are up)
- **Affected Component:** `backend/app/main.py:132-136`, `backend-super/app/main.py:79-99`; FastAPI defaults.
- **Affected User:** Everyone; no auth.
- **Exposed Data:** Complete API inventory with parameter schemas (incl. `internal/bridge/*` routes), hostnames/ports, dependency health.
- **Where Exposed:** `/docs`, `/redoc`, `/api/v1/openapi.json`, `/health` on both apps.
- **How It Was Discovered:** Direct requests (`TENANT_DOCS: 200`, `CP_DOCS: 200`, `/health` payloads shown in §5).
- **Expected Behavior:** In production: `/docs`/openapi disabled or behind admin auth; health minimal ("ok" only) or authenticated.
- **Actual Behavior:** Fully public.
- **Security Impact:** Reconnaissance enabler amplifying C1–C3.
- **Privacy Impact:** n/a.
- **Recommended Fix:** Disable docs in non-debug or gate behind staff auth; trim health output.
- **Regression Risk:** Low.

---

## 3. MEDIUM FINDINGS

| # | Finding | Location | Impact |
|---|---|---|---|
| M1 | Widget token embedded in `sessionStorage` **key names** (`dotstark_session_id:<widgetToken>:<apiBase>`) and in **query strings** (`/widget/config?widget_token=`, `/widget/history?...&widget_token=`, `/ws/widget?widget_token=`) → leaks into browser history, access logs, and Referer headers | `frontend/chat-widget/src/index.js:89-93,125,139,248`; `widget.js` | Token is public-by-design, but query-string placement widens the leak surface; move to headers |
| M2 | Control-plane trusts `X-Forwarded-For` for the IP allowlist (`rbac.py:66-68`) — spoofable if the app is reachable directly (not behind a trusted proxy) | `backend-super/app/core/rbac.py` | Allowlist bypass → C1 amplifier |
| M3 | Approval-queue RBAC mismatch: `POST /approvals` gated by `orgs.delete` can file `billing.refund` requests; either permission can approve the other's request type | `backend-super/app/api/v1/endpoints/approvals.py:74,128,161,262` | Cross-privilege approval paths (mitigated only by second-person check) |
| M4 | `whatsapp_verify_token` (webhook secret) returned **in full** to the tenant admin dashboard | `backend/app/api/v1/endpoints/dashboard.py:1344,1384` | Webhook-secret exposure to UI/clipboard; should be masked like `whatsapp_api_token` |
| M5 | Org `widget_token` returned in full + regenerated copyable; public-by-design but also survives in embed snippets | `dashboard.py:2160-2161,2188-2189`; integrations page UI | Acceptable; ensure revoke flow stays prominent |
| M6 | `DEBUG=true` in **both** `.env` files: control-plane cookies become `Secure=False` (`auth.py:53-63`), tenant OTP generator returns `demo_code` (raw OTP) when DEBUG (`otp.py:40`) | `backend-super/.env`, `backend/.env` | If deployed as-is, cookie theft over http and raw OTP in responses |
| M7 | `detail=str(exc)` echoed in several tenant endpoints (documents PDF failures, dashboard validation) — internal exception text can leak paths/SQL | `documents.py:131,323,411`; `dashboard.py:1475,1783,1827,1870,2003` | Internal detail disclosure; low but real |
| M8 | CORS `"null"` origin + `allow_credentials=True` + permissive `*.trycloudflare.com/cfargotunnel.com` regex on both backends | `backend/app/main.py:96-111`, `backend-super/app/main.py:30-37` | Any sandboxed/file origin can make credentialed requests; over-broad tunnel trust |
| M9 | Control-plane refresh/session rows and login attempts stored, but no IP allowlist default + no account lockout beyond login rate limit | `backend-super/.env`; `auth.py` | Brute-force window for admin passwords (mitigated by login limiter) |

---

## 4. LOW FINDINGS

| # | Finding | Location | Impact |
|---|---|---|---|
| L1 | `dotstark_public_api_base` persisted in localStorage | `frontend/admin-panel/src/app/dashboard/integrations/page.tsx:241,602` | Non-secret; deployment topology leak |
| L2 | Frontends run `next dev` with React DevTools accessible; no `productionBrowserSourceMaps` configured in either build (good) | both `next.config.ts` | Dev-only; production builds have no source maps — OK in prod |
| L3 | Tenant login response and `/auth/me` include `org_id` + email (needed by design) | `app/schemas/auth.py:23-32` | Necessary; informational |
| L4 | `sidebar_state`, timezone, session ids in sessionStorage — non-sensitive | widget + admin-panel | Informational |

---

## 5. INFORMATIONAL FINDINGS

1. **System prompt / internal instructions**: NOT exposed by any public endpoint. The chat widget receives only branding/field schema; agent messages are scrubbed to user-visible text (`widget.py:288-292`). System prompt contains no secrets.
2. **Tool definitions**: the active LangGraph agent binds no tools; no endpoint returns tool schemas.
3. **RAG isolation**: `retrieval_service.py:58-59` hard-filters `WHERE org_id = :org_id` — verified by design; no cross-org chunk retrieval path found.
4. **OTP storage**: hashed (`sha256`) with row-lock verification and 5-attempt cap (`crud/otp.py:63-65,175-213`) — good.
5. **Provider secrets**: Google/Outlook/Zoom refresh tokens, LLM key, Firecrawl key, Stripe key — **never returned** by any endpoint (verified). Stripe secret absent from repo.
6. **Passwords**: bcrypt-hashed (`security.py:21-31`); `hashed_password` never leaves the DB layer.

---

## 6. DATA EXPOSURE MATRIX

| Data | Location | Who Can See It | Who Should See It | Exposed? | Severity |
|---|---|---|---|---|---|
| Password | DB (`users.hashed_password`) | — | — | No (bcrypt, never serialized) | — |
| Password hash | DB only | — | — | No | — |
| JWT (super-admin) | HttpOnly cookie | Admin's browser only | Same | No (cookie) — but **forgeable** via C1 | Critical |
| JWT (tenant) | HttpOnly cookie + login/enter response body | User's browser | Same | Partial (response body includes `access_token`) | Low |
| Refresh token (super-admin) | HttpOnly cookie + hash in DB | Admin only | Same | No | — |
| Widget token | Public embed HTML + query strings + sessionStorage key name | All visitors | All visitors (by design) | Yes (by design) | Medium (M1) |
| Org API key (legacy) | Public HTML when legacy embed used; plaintext DB | All visitors of org's site | Server only | Yes | High (H3) |
| MFA TOTP secret | `/auth/mfa/enroll` response, once | Enrolling admin | Enrolling admin | One-time by design | High (H5, step-up) |
| OTP code | `/auth/security/otp/generate` **when DEBUG** (`demo_code`) | Caller | Caller (should be via email/SMS) | Yes in DEBUG | Medium (M6) |
| Email | Login response, audit `new_values`, dashboard APIs | Own admin / roles with audit.view | Own admin; owner/super-admin only for PII | Over-broad (H2) | High |
| Phone | Booking data (dashboard/API) | Org admin | Org admin | Intended | — |
| Customer name / full_name | Dashboard APIs | Org admin | Org admin | Intended | — |
| Booking data | `/dashboard/bookings` etc. | Org admin (+ control plane by design) | Org admin (+ owner/super-admin) | Intended | — |
| Conversation data | `/widget/history` (own session), control-plane support endpoints | Session owner; support role | Same | Intended; session-scoped (verified 404 cross-org) | — |
| Knowledge-base content | Server-side RAG only; not returned raw | — | — | No (except poisoning C3) | Critical |
| System prompt | Server-side | — | — | No | — |
| RAG chunks | DB + prompt server-side | — | — | No (injectable via C3) | Critical |
| Organization data | In-org APIs; control plane by RBAC | Own admin + roles | Same | Intended | — |
| Billing/Stripe info | Invoices/subscriptions APIs (owner/super-admin/operations) | Billing roles | Same | Intended; provider IDs never exposed | — |
| Audit logs | `/api/v1/audit` etc. | All 5 roles incl. support/auditor | owner/super-admin (PII-unmask) | Over-broad (H2) | High |
| Internal URLs / ports | `/docs`, `/health`, OpenAPI, `dotstark_public_api_base` | Everyone | Staff | Yes (H6, L1) | Medium |
| Database/connection info | health payloads (`admin_db: "ok"`, readonly role names in code) | Everyone | Staff | Partial (H6) | Medium |
| Stack traces | `detail=str(exc)` in few endpoints; generic handler elsewhere | Authenticated users | — | Partial (M7) | Medium |
| Bridge secret | `.env` (published default), no endpoint exposure | Anyone who can read repo /.env | Ops only | In code/config (C2) | Critical |

---

## 7. TENANT ISOLATION MATRIX (verified)

| Resource | Tenant A → Tenant A | Tenant A → Tenant B | Result |
|---|---|---|---|
| Users | ✅ (own org) | 404 (no cross-org users endpoint reachable from tenant API) | PASS |
| Bookings | ✅ `org_id == user.org_id` (`dashboard.py:469`) | 404/empty | PASS |
| Conversations | ✅ session-scoped | **404 "Session not found"** (live-tested with real cross-org session id) | PASS |
| Documents / KB | ✅ `WHERE org_id = :org_id` (`documents.py:198`, `retrieval_service.py:58`) | 404 | PASS |
| Business hours / config | ✅ org-scoped | scope enforced | PASS |
| Integrations / OAuth | ✅ per-org tokens | `state=org_id` confusion risk (H4) | ⚠️ PASS w/ caveat |
| Settings / widget config | ✅ org-scoped | requires own token | PASS |
| Analytics / usage | ✅ org-scoped | — | PASS |
| Audit logs (tenant) | ✅ `get_audit_logs(db, user.org_id, ...)` | — | PASS |

**Live evidence:** cross-org widget history request with a *real* session from org B using org A's widget token → `404 {"detail":"Session not found"}`. Invalid widget token → `404`. Unauthenticated protected endpoints → `401`. Tenant isolation is the strongest part of this system.

---

## 8. AUDIT LOG PRIVACY

- Tenant audit (`app/core/audit.py`): stores hashes? No — stores JSON `old_values`/`new_values`, `ip_address` (`AuditLogResponse` exposes these). Contains customer emails via booking/OTP/impersonation events. **No passwords, hashes, tokens, OTPs**, or API keys found in audit writes (verified: all `new_values` are emails/statuses/ids).
- Control-plane audit (`backend-super/app/core/audit.py`): hash-chained (SHA256 chain, genesis + `compute_record_hash`), stores actor email, IP, UA, and JSON payloads. **No passwords/tokens** — but raw customer emails land in `new_values` (H2).

---

## 9. FINAL VERDICT

1. **Is sensitive data being exposed?** Yes — via **3 exploitable configuration flaws** (C1–C3), all demonstrated live. Tenant isolation itself is solid; browser storage is clean.
2. **Most dangerous exposure:** **C1** — super-admin JWT forgery with the default secret. One HTTP request with a forged token yields the entire control plane (all tenants' PII, billing, impersonation).
3. **Fix FIRST:** **Rotate `SUPER_ADMIN_JWT_SECRET_KEY`** (C1) — it is the highest-impact, zero-code-change fix (`.env` only). Second: rotate `INTERNAL_BRIDGE_SECRET` (C2).
4. **Actual security vulnerabilities:** C1 (forgery), C2 (bridge forgery), C3 (unauth RAG poisoning), H1 (token in URL/log), H2 (over-broad audit PII), H3 (legacy key), H4 (OAuth CSRF-type state).
5. **Informational-only:** system-prompt non-exposure, tool-def non-exposure, RAG isolation, OTP hashing, provider-secret non-exposure, password hashing.
6. **Intentional/admin-visible data:** org API key masked on dashboard, widget token reveal (by design), in-org audit logs for tenants, billing for billing roles.
7. **Frontend-only exposure:** M1 (token in query strings/storage keys), L1 (API base), stash of `demo_code` display (M6 – depends on backend DEBUG).
8. **Requires backend changes:** C1–C3, H2, H3, H4, H5, M2, M4, M7, M8, M9.
9. **Could affect existing functionality if fixed:** H3 (legacy embed breaks if `api_key` acceptance removed), H2 (audit masking may change what support sees), M4 (masking verify token could break UI copy), C3 (webhook signature check must match Firecrawl's real signing header).
10. **Fix before production:** C1, C2, C3, H1, H2, H3, H4, H5, M1, M2, M6.

---

## 10. PRIORITIZED REMEDIATION PLAN

| Priority | Item | Action | Effort |
|---|---|---|---|
| **P0 — Production blocker** | C1 | Rotate `SUPER_ADMIN_JWT_SECRET_KEY` to a strong random value; refuse-to-auth on known defaults | <1h |
| **P0** | C2 | Rotate `INTERNAL_BRIDGE_SECRET`; apply dev-secret guard on the verifying side; loopback-bind bridge | <1h |
| **P0** | C3 | Authenticate Firecrawl webhook (shared secret + crawl ownership check) + per-org ingestion caps | 1-2 days |
| **P1** | H1 | Stop logging full approval/console URLs; `Cache-Control: no-store` on approve/reject | 0.5d |
| **P1** | H2 | Server-side PII masking on audit responses for roles without `pii.unmask` | 1d |
| **P1** | H3 | Audit widget/slots endpoints for `api_key` acceptance; migrate embeds to widget tokens | 1-2d |
| **P1** | H4 | Random OAuth `state` nonce; remove org UUID from public WS payload | 0.5d |
| **P1** | H5 | Rate-limit MFA step-up/verify | 0.5d |
| **P2** | M1 | Move widget token to `X-Widget-Token` header only (drop query-string usage); stop embedding token in storage keys | 1d |
| **P2** | M2 | Validate XFF only when behind known proxy; otherwise use socket IP | 0.5d |
| **P2** | M4 | Mask `whatsapp_verify_token` in dashboard responses | 0.5d |
| **P2** | M6 | Default `DEBUG=false` in both `.env` files | <15m |
| **P2** | M8 | Remove `"null"` origin; restrict tunnel regex or disable in prod | 0.5d |
| **P2** | M9 | DB-backed login lockout + per-org fixed limits | 1d |
| **P3 — Hardening** | M3, M7 | Actor-agnostic approval flow; typed error messages | 1-2d |
| **P3** | H6 | Disable `/docs` in production; minimal `/health` | 0.5d |
| **P3** | L1..L4 | Cleanup: storage keys, dev-mode checks | 0.5d |

---

*Report generated 2026-08-10. All tests were read-only; no data was created, modified, or deleted; no credentials were rotated; no services were restarted.*