# Super Admin — Exact Implementation Plan

Total: 10 phases, 16 weeks. Every phase follows the same internal order:
**DB migration → backend-super endpoints + RBAC permission → bridge endpoint in main backend (if lifecycle write) → frontend page → audit + tests.**

Existing tenant product (FastAPI :8005, Next.js admin panel :3002, widget) is never touched during a phase — all additions are either new apps or additive env-gated endpoints.

---

## Phase 0 — Foundations (Weeks 1–2)

| # | Task | Where | Deliverable |
|---|---|---|---|
| 1 | Create `super_admin` schema | PostgreSQL migration | Tables: `super_admins`, `roles`, `permissions`, `role_permissions`, `admin_mfa`, `admin_sessions`, `refresh_tokens`, `login_attempts` |
| 2 | Create read-only DB role | PostgreSQL | Role `super_admin_readonly`: `GRANT SELECT` on all `public` schema tables; no INSERT/UPDATE/DELETE |
| 3 | Seed RBAC | Migration | 5 roles (Owner, Super Admin, Operations, Support, Auditor) + 26 permissions + 1 bootstrap admin |
| 4 | Add services to compose | docker-compose.yml | `backend-super` (:8006), `frontend/super-admin` (:3003) |
| 5 | Config | backend-super config | `SUPER_ADMIN_JWT_SECRET_KEY`, `INTERNAL_BRIDGE_ENABLED=false`, `TELEMETRY_ENABLED=false`, `SUPER_ADMIN_READONLY_DB_URL` |

## Phase 1 — Auth & RBAC backbone (Weeks 2–3)

| # | Task | Where | Deliverable |
|---|---|---|---|
| 1 | Login / logout endpoints | backend-super | POST `/api/v1/auth/login`, `/logout` |
| 2 | TOTP MFA | backend-super | Enroll (QR), verify, mandatory after first login |
| 3 | JWT issuance | backend-super | Separate issuer, HS256, cookie `super_admin_token` (HttpOnly, Secure) |
| 4 | RBAC middleware | backend-super | Decorator `require_permission()` on every route |
| 5 | Audit framework | backend-super | `super_admin_audit_logs` + hash-chain (previous hash + record hash), DB triggers block UPDATE/DELETE |
| 6 | Frontend shell | frontend/super-admin | Login page, MFA page, layout with sidebar, route guards by role |

## Phase 2 — Tenant data access layer (Weeks 3–4)

| # | Task | Where | Deliverable |
|---|---|---|---|
| 1 | Read-only pool | backend-super | Connection pool bound to `super_admin_readonly` role |
| 2 | Internal bridge | main backend `api/v1/internal/bridge.py` | HMAC-signed service token auth; env-gated `INTERNAL_BRIDGE_ENABLED=false` |
| 3 | Bridge: org lifecycle | main backend | `suspend_org`, `restore_org`, `create_org`, `update_org_config`, `delete_org` |
| 4 | Bridge: user & plan | main backend | `disable_user`, `enable_user`, `reset_password_otp`, `update_plan`, `update_usage_limits` |
| 5 | Bridge: billing | main backend | `retry_payment`, `issue_refund` |
| 6 | Telemetry | main backend | `usage_events` monthly-partitioned counters (bookings, AI tokens, API calls) |
| 7 | Read APIs | backend-super | Orgs, users, bookings, conversations (SELECT via read-only role) |

## Phase 3 — Dashboard + Read side (Weeks 4–6)

| # | Task | Deliverable |
|---|---|---|
| 1 | Dashboard KPIs | Active orgs, bookings/day, MRR, error rate, conversations, AI token usage |
| 2 | Top orgs + active alerts + recent actions | Dashboard widgets |
| 3 | Organizations list/search/filter | Frontend table + backend read API |
| 4 | Organization detail (7 tabs) | Overview, Users, Usage, Billing, Integrations, Audit, Support |
| 5 | Users search/detail | Cross-org user search, profile + bookings + login history |

## Phase 4 — Lifecycle actions (Weeks 6–8) ✅ COMPLETE

| # | Task | Deliverable |
|---|---|---|
| 1 | Suspend / restore org | Bridge calls + confirm dialogs + audit entries |
| 2 | Create org | Onboarding wizard (plan, limits, branding) |
| 3 | Delete org | 2-person approval flow (request → second admin approves → executes) |
| 4 | Disable / enable user | Bridge call + audit |
| 5 | Reset password | OTP flow, audited |
| 6 | Approval & toast UI | Frontend approval queue + confirmations |

Status: backend + frontend done and verified. Backend E2E 37/37 (`%TEMP%\opencode\e2e_phase4.py`); full browser walkthrough on
`:3003` (create org → suspend/restore → file delete → self-approve blocked → second admin approves via MFA-enrolled browser session
→ org deleted, EXECUTED → reject path leaves org intact). Bridge enabled on the dev backend only (`backend/.env` →
`INTERNAL_BRIDGE_ENABLED=true`); `.env.example` / `.env.docker` keep it off for prod until Phase 9 cutover.

## Phase 5 — Plans & Billing (Weeks 8–10) ✅ DONE

| # | Task | Deliverable |
|---|---|---|
| 1 | Plans CRUD | backend-super endpoints + frontend page |
| 2 | Usage limits config | Bookings, AI tokens, seats, storage per plan |
| 3 | Stripe provider | Provider abstraction (Stripe first, others later); invoice list, payment retry, refund |
| 4 | Refund safeguards | Amount threshold + MFA step-up |
| 5 | Revenue reports | MRR, ARR, churn, invoice aging; CSV export |

Status: backend fully done and E2E-verified (part 1: plans 47/47 `%TEMP%\opencode\e2e_phase5.py`;
part 2: billing 41/41 `%TEMP%\opencode\e2e_phase5_billing.py`).
Migration `superadmin_phase5_plans` (applied): `super_admin.plans` (code/price/period/limits JSONB,
NULL = unlimited, whitelisted keys) + `super_admin.plan_assignments` (one ACTIVE row per org, partial
unique index, history on change) + seeded starter/pro/enterprise plans. Endpoints under `/plans`:
CRUD (dup code 409, validation 422s, hard delete vs archive when assigned), `POST /plans/{id}/assign`,
`GET /plans/orgs/{org_id}/plan` (plan + current-month usage), `GET /plans/orgs/{org_id}/usage`
(usage_events read). RBAC via seeded `plans.view/manage/assign` (operations role has them, support
does not); all writes audited.

Migration `superadmin_phase5_billing` (applied): `super_admin.subscriptions` (one non-canceled row
per org, partial unique index, provider-agnostic provider/provider_sub_id) + `super_admin.invoices`
(open → paid|refunded|void|uncollectible) + permission `billing.manage` (owner/super_admin/operations).
Endpoints under `/billing`: subscriptions create/list (409 duplicate, 422 archived plan),
invoices issue/list, `POST /invoices/{id}/retry` (409 non-open), `POST /invoices/{id}/refund` —
requires MFA step-up cookie (`POST /auth/mfa/stepup`, 5-min TOTP re-verify) always; amounts above
`REFUND_APPROVAL_THRESHOLD_CENTS` (50000) instead file a `billing.refund` approval (24h expiry,
second-person rule) that a second admin approves; executor runs the shared `execute_refund`.
`/billing/reports/revenue` (MRR/ARR/churn/aging) + `.csv`. Provider abstraction in `app/core/billing.py`
(MockProvider deterministic; StripeProvider auto-selected when `STRIPE_SECRET_KEY` set).
Bugs fixed during E2E: `:id::uuid` cast → `CAST(:id AS uuid)` (asyncpg), `updated_at` on invoices
(removed), `payload` shadowing in approval executor.

Frontend done and browser-verified (screenshots `docs/phase5-plans-page.png`,
`docs/phase5-billing-page.png`): `/dashboard/plans` (list + create/edit modals with limit fields
blank = unlimited, archive with confirm, assign with org search; `plans.manage`/`plans.assign`
gated) and `/dashboard/billing` (tabs Revenue/Subscriptions/Invoices; MRR/ARR/churn cards, invoice
aging table, CSV export; create subscription + issue invoice modals with org search; retry; refund
modal with reason + TOTP step-up → PENDING_APPROVAL toast for amounts above the $500 threshold;
approval visible on Approvals page and executed refund reflected in the UI). Pre-existing TS errors
in the org detail page (discriminated-union narrowing) also fixed; `tsc --noEmit` clean.
Remaining: none — Phase 5 complete (UI keep-out wiring for usage limits lands with the tenant
middleware work).

## Phase 6 — Analytics & Monitoring (Weeks 10–12)

> **Status: ✅ DONE (backend)** — E2E `%TEMP%\opencode\e2e_phase6.py` 61/61 green against
> live servers (tenant :8005 + backend-super :8006).
>
> Backend delivered:
> - Migration `superadmin_phase6_analytics`: `super_admin.incidents` table (severity
>   critical|major|minor, status open|investigating|resolved, created_by/resolved_by/resolved_at)
>   + index; 6 permissions (`reports.view`, `health.view`, `alerts.view`, `alerts.manage`,
>   `incidents.view`, `incidents.manage`) seeded; owner/super_admin get all, operations gets
>   `reports.view`/`health.view`/`alerts.view`/`incidents.view` (Phase 0 already granted ops
>   `alerts.manage`/`incidents.manage`), support gets none.
> - Reports engine `backend-super/app/api/v1/endpoints/reports.py`: 12 report types listed
>   (usage, revenue, bookings, conversations, ai, api, storage, kb, growth, performance, health,
>   widget); live SQL builders over `usage_events` + tenant tables; JSON (`GET /reports/{name}`,
>   month filter for usage) + CSV (`GET /reports/{name}/csv`); revenue intentionally 404s with a
>   pointer to `/billing/reports/revenue.csv` (billing owns it).
> - Dependency health: `GET /health/dependencies` proxies tenant bridge
>   `/internal/bridge/dependency-health` (DB/LLM/email checks, HMAC-signed); `GET /health/overview`
>   aggregates open/critical alerts + open incidents.
> - Platform alerts `backend-super/app/api/v1/endpoints/alerts.py`: list + filters
>   (severity/status/component/org), trends, get; acknowledge/resolve write through the tenant
>   bridge (tenant-side audit + counters) with idempotency; platform audit rows
>   (`alerts.acknowledge`/`alerts.resolve`).
> - Incidents `backend-super/app/api/v1/endpoints/incidents.py`: create/get/list
>   (severity/status filters)/update (investigating → resolved clears/reopens resolved_at +
>   resolved_by); validation 400s; audit rows `incidents.create`/`incidents.update`.
> - Main backend: bridge routes `GET /internal/bridge/dependency-health`,
>   `POST /internal/bridge/alerts/{id}/acknowledge|resolve`; `AuditAction.ALERT_ACKNOWLEDGE`/
>   `ALERT_RESOLVE`; fixed `Request` import in bridge.py (was crashing tenant startup).
> - E2E covers report JSON+CSV (incl. month 422 / unknown 404), dependency health shape, alert
>   ack/resolve idempotency via bridge, incident lifecycle, RBAC (ops read-only 200s, manage 200
>   per Phase 0 grants; support 403 everywhere), audit rows, full cleanup.
>
> Not yet done (frontend + remaining): Phase 6 frontend (reports/alerts/incidents pages), alert
> creation from tenant side already exists; task 3's Redis/Firecrawl/Calendar probes not
> implemented (DB/LLM/email only), task 5's incident notify (email) not implemented.

**Update 2026-08-05 — remaining Phase 6 closed:**

- **Frontend done and browser-verified (live against :3003/:8005/:8006):** `/dashboard/reports`
  (12 report types with month filter, JSON + CSV export), `/dashboard/alerts` (severity/status
  filters, ack/resolve), `/dashboard/incidents` (status/severity filters, create + lifecycle),
  `/dashboard/monitoring` renders all five dependency checks.
- **Task 3 probes:** `build_dependency_health_payload` (main backend) now also checks
  `firecrawl` (FIRECRAWL_API_KEY) and `calendar` (GOOGLE_CLIENT_ID/SECRET or
  MICROSOFT_CLIENT_ID/SECRET, status `degraded` with error when absent). Redis is deliberately
  NOT probed — Redis was never part of this stack (no code path uses it), so a check would
  report false alarms; the plan's Redis entry is dropped for that reason.
- **Task 5 incident notify:** `backend-super/app/core/incident_notify.py` — SMTP email
  (`SMTP_HOST/PORT/USER/PASSWORD/FROM`, recipients `INCIDENT_NOTIFY_TO` comma-separated;
  STARTTLS on 587) sent on incident create and on severity escalation to critical/major;
  fail-open (errors logged, never block CRUD), no-op when unconfigured.

| # | Task | Deliverable |
|---|---|---|
| 1 | 12 report types | Revenue, Usage, Performance, Growth, Health, Storage, AI, API, Bookings, Conversations, KB, Widget |
| 2 | Report engine | Query builder over `usage_events` + tenant tables + CSV export |
| 3 | Dependency health | Health probes: DB, LLM, Firecrawl, Email, Calendar (health endpoint in main backend, consumed by backend-super); Redis not part of the stack — intentionally no probe |
| 4 | Alerts | Create, acknowledge, resolve; alert inbox on dashboard |
| 5 | Incidents | Create, notify (email — SMTP, create + escalation), resolve; status shown on dashboard |

## Phase 7 — Ops tools (Weeks 12–13)

| # | Task | Deliverable |
|---|---|---|
| 1 | Feature flag registry | DB-backed flags; main backend reads flags on every request |
| 2 | Toggle + scoping | Platform-wide, per-org, per-plan |
| 3 | Canary rollout | % of orgs flag, graduated release |
| 4 | Kill switch | Instant platform-wide disable of any feature |
| 5 | Broadcast notifications | Draft → review → send to orgs or staff; audit trail |

## Phase 8 — Support & Security (Weeks 13–14)

| # | Task | Deliverable |
|---|---|---|
| 1 | Impersonation | MFA step-up → 30-min token targeting an org admin → single active session → full audit log + on-screen "you are impersonating" bar |
| 2 | Conversation peek | Read-only transcripts; PII masking (emails, phones, names) applied before display |
| 3 | Step-up auth | Second TOTP for sensitive actions (refund, delete, impersonate, bridge writes) |
| 4 | Security page | MFA policy, password policy, IP allowlist |

## Phase 9 — Hardening & GA (Weeks 14–16)

| # | Task | Deliverable |
|---|---|---|
| 1 | PII masking defaults | Masked by default; unmask requires permission |
| 2 | Audit chain verification | Test that hash-chain breaks are detected |
| 3 | E2E tests | All 43 features, happy + failure paths |
| 4 | Load tests | Dashboard + analytics under concurrent admin usage |
| 5 | Usage backfill | Migrate existing data into `usage_events` |
| 6 | Cutover + runbooks | Enable `INTERNAL_BRIDGE_ENABLED`, `TELEMETRY_ENABLED`; incident runbooks, rollback plan |

---

## Feature → Phase mapping (43 features, 13 modules)

| Module | Features | Phase |
|---|---|---|
| Dashboard | 4 features (KPIs, top orgs, alerts, recent actions) | P3 |
| Organizations | 7 (list, detail, create, suspend, restore, export, delete) | P3–P4 |
| Users | 4 (search, detail, disable/enable, reset) | P3–P4 |
| Plans | 3 (CRUD, limits, assign/upgrade) | P5 |
| Billing | 4 (invoices, retry, refunds, revenue reports) | P5 |
| Analytics | 2 (12 report types, CSV export) | P6 |
| Monitoring | 3 (dependency health, alerts, incidents) | P6 |
| Feature Flags | 4 (toggle, canary, scope, kill switch) | P7 |
| Audit Logs | 2 (trail, tamper-proof) | P1 |
| Notifications | 2 (broadcasts, workflow) | P7 |
| Support | 2 (impersonation, conversation peek) | P8 |
| Settings | 2 (branding, security policy) | P4 / P8 |
| Security | 4 (RBAC, MFA, step-up, PII masking) | P1 / P8–P9 |

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Bridge misused in prod | `INTERNAL_BRIDGE_ENABLED=false` until GA; HMAC per-service token; per-endpoint audit |
| Read-only role accidentally upgraded | Separate credentials, never reused; monitor role grants |
| Audit tampering | Hash chain + UPDATE/DELETE triggers; verify on restore drills |
| Refund abuse | Threshold + step-up MFA + audit |
| Impersonation misuse | 30-min expiry, single session, mandatory audit entry, visible banner |
