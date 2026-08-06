"""
Phase 9.2 E2E - Audit integrity: append-only trigger + hash-chain tamper detection.

Layer 1 (DB): UPDATE/DELETE on super_admin.audit_logs is rejected by triggers
             audit_logs_no_update / audit_logs_no_delete.
Layer 2 (app): verify_chain() recomputes every record hash; any historical edit
             (even with triggers bypassed) breaks the chain and is detected.

Tampering is simulated with a postgres superuser connection running with
session_replication_role=replica so triggers are skipped, proving the hash
chain is the final line of defense. All rows are restored afterwards.

Run from backend-super dir with its venv python.
"""
import asyncio
import sys

from sqlalchemy import text

from app.core.audit import GENESIS_HASH, verify_chain
from app.db.database import AsyncSessionLocal

import asyncpg

PG_DSN = "postgresql://postgres:dotstark_secure_pass@localhost:5432/dotstark_v2"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}{(' — ' + extra) if extra else ''}")
    else:
        FAIL += 1
        print(f"  FAIL {name}{(' — ' + extra) if extra else ''}")


async def main() -> int:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, action, previous_hash, record_hash, created_at
                    FROM super_admin.audit_logs
                    ORDER BY created_at ASC, id ASC
                    """
                )
            )
        ).mappings().all()

        check("audit table has records to test", len(rows) >= 3, f"{len(rows)} rows")

        # ── 1. Baseline ────────────────────────────────────────────────
        baseline = await verify_chain(db)
        check(
            "baseline chain valid",
            baseline["chain_valid"],
            f"total={baseline['total_records']}",
        )

        target_idx = len(rows) // 2
        target = rows[target_idx]
        link_row = rows[target_idx + 1] if target_idx + 1 < len(rows) else rows[target_idx]

        # ── 2. Append-only trigger blocks UPDATE/DELETE (app user) ─────
        try:
            await db.execute(
                text(
                    "UPDATE super_admin.audit_logs SET action = 'x' WHERE id = :id"
                ),
                {"id": target["id"]},
            )
            await db.commit()
            check("app user UPDATE blocked", False, "update unexpectedly allowed")
        except Exception as e:
            check(
                "app user UPDATE blocked",
                "RaiseError" in type(e).__name__ or "append-only" in str(e),
                str(e)[:80],
            )
            await db.rollback()

        try:
            await db.execute(
                text("DELETE FROM super_admin.audit_logs WHERE id = :id"),
                {"id": target["id"]},
            )
            await db.commit()
            check("app user DELETE blocked", False, "delete unexpectedly allowed")
        except Exception as e:
            check(
                "app user DELETE blocked",
                "RaiseError" in type(e).__name__ or "append-only" in str(e),
                str(e)[:80],
            )
            await db.rollback()

        # ── 3. Simulated tamper (superuser, triggers bypassed) ─────────
        pg = await asyncpg.connect(PG_DSN)
        try:
            # copy the migration's trigger body bypass switch
            await pg.execute("SET session_replication_role = replica")

            original_action = target["action"]
            await pg.execute(
                "UPDATE super_admin.audit_logs SET action = $1 WHERE id = $2",
                "audit.chain.tamper",
                target["id"],
            )

            after = await verify_chain(db)
            check(
                "content tamper detected",
                not after["chain_valid"],
                f"first_broken_index={after['first_broken_index']} expected={target_idx}",
            )
            check(
                "tamper broken at exact row",
                after["first_broken_index"] == target_idx,
                f"got {after['first_broken_index']}",
            )

            await pg.execute(
                "UPDATE super_admin.audit_logs SET action = $1 WHERE id = $2",
                original_action,
                target["id"],
            )
            restored = await verify_chain(db)
            check("restore -> chain valid again", restored["chain_valid"])

            # ── 4. previous_hash link tamper ───────────────────────────
            orig_prev = link_row["previous_hash"]
            fake_prev = "1" * 64 if orig_prev != "1" * 64 else "2" * 64
            await pg.execute(
                "UPDATE super_admin.audit_logs SET previous_hash = $1 WHERE id = $2",
                fake_prev,
                link_row["id"],
            )
            after_link = await verify_chain(db)
            check(
                "previous_hash link tamper detected",
                not after_link["chain_valid"],
                f"first_broken_index={after_link['first_broken_index']}",
            )
            await pg.execute(
                "UPDATE super_admin.audit_logs SET previous_hash = $1 WHERE id = $2",
                orig_prev,
                link_row["id"],
            )
            final = await verify_chain(db)
            check(
                "link restore -> chain valid again",
                final["chain_valid"],
                f"total={final['total_records']}",
            )
        finally:
            await pg.execute("SET session_replication_role = origin")
            await pg.close()

        # ── 5. Genesis anchor ──────────────────────────────────────────
        check("first row anchored to genesis", rows[0]["previous_hash"] == GENESIS_HASH)

    print(f"\nPhase 9.2 audit tamper E2E: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
