"""
Phase 9.3 E2E - Consolidated run across all 43 super-admin features.

Runs every phase E2E suite as a subprocess (backend-super venv python) and
aggregates the results:

  phase 1  RBAC + audit trail        (Security, Audit Logs)
  phase 2  MFA, sessions, tokens     (Security)
  phase 3  Dashboard + org/user tabs (Dashboard, Organizations, Users)
  phase 4  org lifecycle + security  (Organizations, Settings)
  phase 5  plans + billing           (Plans, Billing)
  phase 6  analytics + monitoring    (Analytics, Monitoring)
  phase 7  feature flags + notif     (Feature Flags, Notifications)
  phase 8  support + security policy (Support, Settings, Security)
  phase 9.1 PII masking              (Security)
  phase 9.2 audit tamper detection   (Audit Logs)

Prereqs: backend-super running on :8006 with LOGIN_RATE_LIMIT=120/minute
(start it via `start-super-with-e2e` helper or manually), Postgres up, .env
in backend-super. After E2E runs, restart backend-super WITHOUT the env var
so the production default 10/minute login limit is restored.
"""
import asyncio
import os
import subprocess
import sys

from pathlib import Path

import asyncpg

VENV_PY = r"C:\Users\priyanshuraj\OneDrive\Desktop\dotstark\booking_agent\backend-super\venv\Scripts\python.exe"
SCRIPTS_DIR = Path(__file__).parent
BACKEND_SUPER = r"C:\Users\priyanshuraj\OneDrive\Desktop\dotstark\booking_agent\backend-super"
DB_DSN = "postgresql://postgres:dotstark_secure_pass@localhost:5432/dotstark_v2"

SUITES = [
    ("phase 1  RBAC + audit trail", "e2e_phase1.py"),
    ("phase 2  MFA + sessions", "e2e_phase2.py"),
    ("phase 3  dashboard + tabs", "e2e_phase3.py"),
    ("phase 4  org lifecycle + security", "e2e_phase4.py"),
    ("phase 5  plans + billing", "e2e_phase5.py"),
    ("phase 5b billing workflows", "e2e_phase5_billing.py"),
    ("phase 6  analytics + monitoring", "e2e_phase6.py"),
    ("phase 7  flags + notifications", "e2e_phase7.py"),
    ("phase 8  support + security policy", "e2e_phase8.py"),
    ("phase 9.1 PII masking", "e2e_phase9_pii_masking.py"),
    ("phase 9.2 audit tamper detection", "e2e_phase9_audit_tamper.py"),
]


def reset_lockout() -> None:
    """Clear failed-login lockout state accumulated by failure-path tests.

    The lockout threshold is 5 failed attempts / 15 min (backend-super
    auth.py). Every phase deliberately tests a wrong password, so a full
    consolidated run would trip the persistent lockout and poison the
    remaining phases. This is test state, not production data.
    """
    async def _reset():
        conn = await asyncpg.connect(DB_DSN)
        try:
            deleted = await conn.execute(
                "DELETE FROM super_admin.login_attempts WHERE success = false"
            )
            print(f"  [reset] cleared failed login_attempts ({deleted})")
        finally:
            await conn.close()

    asyncio.run(_reset())


def main() -> int:
    results = []
    overall = 0
    print("=" * 70)
    print("Phase 9.3 consolidated E2E - 43 features, happy + failure paths")
    print("=" * 70)
    reset_lockout()
    for label, script in SUITES:
        print(f"\n--- {label} ---")
        reset_lockout()
        proc = subprocess.run(
            [VENV_PY, str(SCRIPTS_DIR / script)],
            cwd=BACKEND_SUPER,
            env={
                **os.environ,
                "PYTHONPATH": BACKEND_SUPER,
                "PYTHONIOENCODING": "utf-8",
            },
            capture_output=True,
            text=True,
        )
        # surface the suite's own PASS/FAIL lines
        for line in proc.stdout.splitlines():
            if line.startswith("  PASS") or line.startswith("  FAIL") or "passed" in line:
                print(line)
        if proc.returncode != 0:
            overall += 1
            results.append((label, "FAIL"))
            print(f"  >>> {label}: FAILED (exit {proc.returncode})")
            tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
            if tail:
                print("  stderr tail:", tail[:400])
        else:
            results.append((label, "PASS"))
            print(f"  >>> {label}: PASS")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for label, status in results:
        print(f"  [{status}] {label}")
    print(f"\nConsolidated: {len(results) - overall} passed, {overall} failed")
    return 1 if overall else 0


if __name__ == "__main__":
    sys.exit(main())
