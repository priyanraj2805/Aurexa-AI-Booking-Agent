# Super Admin E2E test suites (Phase 9)

Regression suites for the backend-super control plane. Every suite is
idempotent and self-cleaning (test admins/orgs are reused or removed;
lockout state is reset by the consolidated runner).

## Prerequisites

- PostgreSQL `dotstark_v2` reachable (postgres / `dotstark_secure_pass`)
- backend-super running on `http://127.0.0.1:8006`
- Python: `backend-super\venv\Scripts\python.exe` (has `httpx`, `pyotp`,
  `asyncpg`, `passlib`)

## Run everything

```powershell
# 1. Start backend-super with a raised login rate limit for the run:
$env:LOGIN_RATE_LIMIT = "120/minute"
# ...start uvicorn app.main:app --port 8006 with that env...
# 2. Run the consolidated suite (11 phases, 43 features):
& backend-super\venv\Scripts\python.exe scripts\e2e\e2e_phase9_consolidated.py
# 3. Restore the production default afterwards: restart backend-super
#    WITHOUT LOGIN_RATE_LIMIT set (default 10/minute).
```

## Individual suites

| Script | Covers | Notes |
|---|---|---|
| `e2e_phase1.py` | RBAC, audit trail, CSRF, lockout | Ends by deliberately triggering lockout (6th wrong attempt → 429); clear `super_admin.login_attempts` before the next run |
| `e2e_phase2.py` | MFA, sessions, token refresh | |
| `e2e_phase3.py` | Dashboard, orgs, users tabs | |
| `e2e_phase4.py` | Lifecycle, two-person approvals | Requires MFA step-up for approvers |
| `e2e_phase5.py` | Plans CRUD, limits, assign/upgrade | |
| `e2e_phase5_billing.py` | Invoices, refunds (step-up), revenue | |
| `e2e_phase6.py` | 12 report types, CSV, alerts | |
| `e2e_phase7.py` | Feature flags, notifications | |
| `e2e_phase8.py` | Support, impersonation, security policy | |
| `e2e_phase9_pii_masking.py` | PII masked by default; unmask permission | |
| `e2e_phase9_audit_tamper.py` | Audit chain: UPDATE/DELETE blocked, break detection, restore | |
| `e2e_phase9_load.py` | Throughput/latency: sequential baseline + 500-request concurrent burst | Auth once; safe under default rate limit |
| `e2e_phase9_backfill.py` | Reprojects historical bookings/lifecycle into `usage_events` | DB-only, idempotent; run once at cutover (P9.5) |

## Troubleshooting

- `429 Rate limit exceeded` — server is at the default `10/minute`; run the
  consolidated suite with `LOGIN_RATE_LIMIT=120/minute`, or wait a minute.
- `429 Too many failed attempts` — persistent lockout rows in
  `super_admin.login_attempts` (5 failed / 15 min). Delete them:
  `DELETE FROM super_admin.login_attempts WHERE success = false;`
- `no partition of relation "usage_events" found` — a partition for the
  target month is missing; `e2e_phase9_backfill.py` creates them itself.
