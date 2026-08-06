# Super Admin Cutover & Runbooks

> Phase 9.6 — flipping the control plane on for GA: gate enablement, post-cutover
> verification, incident runbooks, and the rollback plan.

## 1. What "cutover" means

The Super Admin control plane ships fully implemented but **disabled by default**.
Two environment gates keep the main tenant backend behavior-identical until GA:

| Gate | Location | Default | Effect when ON |
|---|---|---|---|
| `INTERNAL_BRIDGE_ENABLED` | `backend/.env` | `false` | Registers `app/api/v1/internal/bridge.py`; HMAC-signed calls from backend-super can execute lifecycle actions (org create/suspend/restore/delete, user disable/enable, impersonation) |
| `TELEMETRY_ENABLED` | `backend/.env` | `false` | `increment_usage()` writes monthly counters into `super_admin.usage_events` |
| `FEATURE_FLAGS_ENABLED` | `backend/.env` | `false` | Main backend evaluates `super_admin.feature_flags` on requests (kill switches, canaries) |

backend-super (control plane) additionally needs its own envs: `INTERNAL_API_URL`
(URL of the main backend, default `http://localhost:8005`), `INTERNAL_BRIDGE_SECRET`
(must match the main backend's), and `LOGIN_RATE_LIMIT` (default `10/minute` —
do **not** raise it in production).

## 2. Pre-cutover checklist (Phase 9 hardening)

- [ ] P9.1 PII masking: `backend-super/app/core/pii.py` masking applied at every
      tenant-data endpoint; unmask requires `pii.unmask` (owner-only); frontend
      shows masked-by-default.
- [ ] P9.2 Audit tamper detection: hash chain + append-only triggers verified by
      `scripts/e2e/e2e_phase9_audit_tamper.py` (UPDATE/DELETE blocked, break detected, restore).
- [ ] P9.3 Consolidated E2E: `scripts/e2e/e2e_phase9_consolidated.py` — all 11
      suites pass (43 features, happy + failure paths). Requires backend-super
      started with `LOGIN_RATE_LIMIT=120/minute`; restore the default after the run.
- [ ] P9.4 Load tests: `scripts/e2e/e2e_phase9_load.py` — <1% errors, p95 < 1000ms
      under a 500-request concurrent burst on a single worker.
- [ ] P9.5 Usage backfill: `scripts/e2e/e2e_phase9_backfill.py` run once;
      bookings/lifecycle metrics reprojected into `usage_events` (idempotent;
      `ai_tokens` has no historical source and accumulates from cutover on).
      Re-run after cutover to sweep up bookings created in between. Live
      wiring: confirmed bookings increment `bookings` (crud/booking.py) and
      each chat turn increments `ai_tokens` (engine/agent/graph.py), both
      gated on `TELEMETRY_ENABLED`.
- [ ] Backups: full Postgres dump taken and verified restorable.

## 3. Cutover steps

### 3.1 Enable gates on the main backend

```bash
# backend/.env
INTERNAL_BRIDGE_ENABLED=true
INTERNAL_BRIDGE_SECRET=<64-char-random>     # same value in backend-super/.env
TELEMETRY_ENABLED=true
FEATURE_FLAGS_ENABLED=true                  # optional — flags default to caller defaults
```

```bash
cd backend
venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8005
```

Verify the bridge is registered (was 503 before):

```bash
curl -s http://localhost:8005/api/v1/internal/bridge/dependency-health -o /dev/null -w "%{http_code}"
# 401 (HMAC rejected, no signature headers) = registered + auth enforced
# 503 "Internal bridge is disabled" = still gated off
```

### 3.2 Start backend-super with matching secret

```bash
# backend-super/.env
INTERNAL_BRIDGE_SECRET=<same-64-char-random>
INTERNAL_API_URL=http://localhost:8005
SUPER_ADMIN_JWT_SECRET_KEY=<64-char-random>
LOGIN_RATE_LIMIT=10/minute
```

```bash
cd backend-super
venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8006 --workers 2
```

### 3.3 Post-cutover verification

1. `e2e_phase9_consolidated.py` (with the elevated rate limit, then restore).
2. Owner login → MFA → dashboard shows real KPI numbers (bookings now counted).
3. Reports → `usage` for the current month shows live `bookings` counters.
4. Lifecycle test on a disposable org: suspend/restore from the UI, confirm the
   tenant-side row flips and `usage_events` gains `org.suspend`/`org.restore`.
5. Feature flag kill switch: flip a flag off in the control plane, confirm the
   tenant backend honors it (gated feature disabled within TTL).
6. Impersonation: start a session, confirm 30-min expiry + mandatory audit entry.

## 4. Incident runbooks

### 4.1 Bridge failures (lifecycle actions 500 / time out)

- **Detect**: `audit_logs` shows the approval reaching `EXECUTED` but the tenant
  row unchanged; backend-super logs `bridge` errors.
- **Triage**: check `INTERNAL_API_URL` reachability, `INTERNAL_BRIDGE_SECRET`
  match, clock skew (`INTERNAL_BRIDGE_MAX_SKEW_SECONDS`, default 300s).
- **Fix**: restart backend-super to retry failed actions; if the main backend is
  down, hold approvals — they are idempotent and safe to re-approve.
- **Escalate**: if secret mismatch, rotate `INTERNAL_BRIDGE_SECRET` on both sides.

### 4.2 Telemetry not writing usage_events

- **Detect**: usage report flat at 0 while bookings happen.
- **Triage**: confirm `TELEMETRY_ENABLED=true` on the main backend; check
  `usage_events` partition for the current month exists
  (`usage_events_YYYY_MM`); check backend logs for "Failed to write usage event".
- **Fix**: re-run the P9.5 backfill (idempotent) to close the gap; the partition
  trigger auto-creates partitions on INSERT — verify the trigger exists:
  `\df super_admin.ensure_usage_partition`.

### 4.3 Audit chain break detected

- **Detect**: control-plane audit page warns "chain broken"; verify endpoint
  returns `chain_valid: false`.
- **Triage**: identify `first_broken_index`; determine whether the break came
  from a manual DB edit (PG triggers block UPDATE/DELETE on `audit_logs`) or a
  corrupt restore.
- **Fix**: restore the missing/mutated row from the pre-cutover backup dump;
  re-run `scripts/e2e/e2e_phase9_audit_tamper.py` to confirm `chain_valid: true`.
- **Escalate**: treat any unexplained break as a security incident — rotate
  admin credentials, audit sessions.

### 4.4 Brute-force / account lockout

- **Detect**: owner reports "Too many failed attempts"; `login_attempts` has 5+
  failed rows within 15 min.
- **Triage**: confirm the IP (all lockout is per-IP + per-email); the 10/min
  slowapi limiter is a separate layer (returns 429 without writing a row).
- **Fix**: blocked attempts clear automatically after 15 min. For legitimate
  lockouts, delete the failed rows
  (`DELETE FROM super_admin.login_attempts WHERE email = '<admin>'`) — this is
  test/operator data only, never touches `audit_logs`.

### 4.5 Impersonation session misuse

- **Detect**: support admin impersonating; audit entry `impersonation.start`
  exists; session banner visible to the impersonator.
- **Triage**: confirm the 30-min expiry is enforced (the tenant session dies at
  expiry), single active session per target.
- **Fix**: force-kill via the control plane; revoke the support admin if abused.
- **Escalate**: mandatory `impersonation.start` audit row is the ground truth —
  export it before any DB work.

### 4.6 Billing refund anomalies

- **Detect**: refunds exceeding `REFUND_APPROVAL_THRESHOLD_CENTS` (50000) without
  a second-admin approval audit trail.
- **Triage**: refunds below the threshold need step-up MFA only; above it need
  MFA step-up **and** a second admin's approval — both are audited.
- **Fix**: reject/void the refund in the provider; disable the offending admin.
- **Escalate**: full payment + audit export.

## 5. Rollback plan

Rollback is safe at any time because every gate is a no-op when OFF — the main
backend returns to pre-cutover behavior with **no data migration** involved.

| Step | Action | Effect |
|---|---|---|
| 1 | Set `INTERNAL_BRIDGE_ENABLED=false`, `TELEMETRY_ENABLED=false`, `FEATURE_FLAGS_ENABLED=false` in `backend/.env` | Bridge 404s; telemetry stops writing; flags short-circuit to defaults |
| 2 | Restart the main backend | Behavior identical to pre-cutover |
| 3 | Keep backend-super running or stop it | Control plane is read-mostly; writes are the only casualty. Lifecycle actions fail with 404 until roll-forward |
| 4 | Leave `usage_events` data in place | Harmless; the P9.5 backfill + telemetry can resume on roll-forward |

Rollback of the control plane itself (backend-super down): tenant-side features
are unaffected — only lifecycle control, reports, and support tooling disappear.

**Roll-forward** = re-run section 3.1-3.3.

## 6. Related documents

| Document | What it covers |
|---|---|
| [Implementation Plan](../SUPERADMIN_IMPLEMENTATION_PLAN.md) | Phase roadmap + threat model |
| [Environment Variables](../reference/environment-vars.md) | All config options |
| [Troubleshooting](../reference/troubleshooting.md) | Common issues |
| [Production Deployment](production.md) | Main platform deployment |
