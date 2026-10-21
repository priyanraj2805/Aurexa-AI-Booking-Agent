#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# db-migrate.sh — move the Postgres data between the laptop and the GCP VM.
#
#   Usage:
#     bash scripts/db-migrate.sh dump                 # laptop  -> ./db-dump-<ts>.dump
#     bash scripts/db-migrate.sh restore db-dump.dump # VM     -> postgres container
#
# The dump is a pg_dump CUSTOM format (-Fc) so it can be restored with
# pg_restore (parallel-capable, and it carries the schema + data + indexes).
#
# IMPORTANT: the POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB of the
# target must match the source, otherwise the restored rows land in a
# different database than the app reads. The .env files copied to the VM are
# byte-identical to the laptop's, so they already match.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[fail] %s\033[0m\n' "$*" >&2; exit 1; }

# Read POSTGRES_* out of the root .env (compose reads it from there).
env_get() {
  local key="$1"
  [ -f .env ] || return 0
  grep -E "^${key}=" .env | head -1 | cut -d= -f2-
}
PGUSER_NAME="$(env_get POSTGRES_USER)"; PGUSER_NAME="${PGUSER_NAME:-postgres}"
PGDB_NAME="$(env_get POSTGRES_DB)";     PGDB_NAME="${PGDB_NAME:-dotstark_v2}"

CMD="${1:-}"
case "$CMD" in

  dump)
    say "Dumping ${PGDB_NAME} from the local postgres container"
    # The dump is written INSIDE the container and copied out with `docker cp`.
    # NEVER redirect pg_dump straight into a file with `>` — PowerShell
    # rewrites the bytes as text and the archive comes out corrupt
    # ("input file does not appear to be a valid archive").
    OUT="db-dump-$(date +%Y%m%d-%H%M%S).dump"
    docker compose exec -T postgres \
      pg_dump -U "$PGUSER_NAME" -Fc -f /tmp/db-migrate.dump "$PGDB_NAME"
    docker cp "$(docker compose ps -q postgres):/tmp/db-migrate.dump" "./$OUT"
    docker compose exec -T postgres rm -f /tmp/db-migrate.dump
    ls -lh "$OUT"
    say "Now copy it to the VM:"
    echo "  gcloud compute scp $OUT <VM_NAME>:/tmp/ --zone=<ZONE>"
    ;;

  restore)
    FILE="${2:-}"
    [ -n "$FILE" ] && [ -f "$FILE" ] || die "Usage: bash scripts/db-migrate.sh restore <dump-file>"
    say "Restoring ${FILE} into ${PGDB_NAME}"
    # The container must be up and the target database must exist.
    docker compose up -d postgres
    # Postgres needs ~10s to initialise a fresh volume. A single check
    # right after "up -d" fails on a brand new VM.
    printf "  waiting for postgres"
    _ready=0
    for _i in $(seq 1 60); do
      if docker compose exec -T postgres pg_isready -U "$PGUSER_NAME" -d "$PGDB_NAME" >/dev/null 2>&1; then
        _ready=1
        echo " ready after $((_i * 2))s"
        break
      fi
      printf "."
      sleep 2
    done
    echo ""
    [ "$_ready" = "1" ] || die "postgres did not become ready in 120s"
    docker compose exec -T postgres psql -U "$PGUSER_NAME" -lqt \
      | cut -d'|' -f1 | tr -d ' ' | grep -qx "$PGDB_NAME" \
      || die "Database ${PGDB_NAME} does not exist inside the container."
    docker cp "$FILE" "$(docker compose ps -q postgres):/tmp/restore.dump"
    # --clean drops existing objects first so a re-restore is idempotent.
    docker compose exec -T postgres pg_restore -U "$PGUSER_NAME" -d "$PGDB_NAME" \
      --clean --if-exists --no-owner --no-privileges /tmp/restore.dump \
      || warn "pg_restore reported errors — check: docker compose logs postgres"
    docker compose exec -T postgres rm -f /tmp/restore.dump
    # The source database carries orphaned rows (a super_admin row was
    # deleted without the FKs existing), so pg_restore cannot create three
    # constraints in the super_admin schema. Clean the orphans and add the
    # constraints back so the schema matches what the migrations expect.
    say "Repairing orphaned super_admin rows"
    docker compose exec -T postgres psql -U "$PGUSER_NAME" -d "$PGDB_NAME" -q <<'SQL'
DELETE FROM super_admin.admin_sessions WHERE admin_id IS NOT NULL AND admin_id NOT IN (SELECT id FROM super_admin.super_admins);
DELETE FROM super_admin.audit_logs WHERE actor_id IS NOT NULL AND actor_id NOT IN (SELECT id FROM super_admin.super_admins);
DELETE FROM super_admin.login_attempts WHERE admin_id IS NOT NULL AND admin_id NOT IN (SELECT id FROM super_admin.super_admins);
ALTER TABLE super_admin.admin_sessions DROP CONSTRAINT IF EXISTS admin_sessions_admin_id_fkey;
ALTER TABLE super_admin.audit_logs DROP CONSTRAINT IF EXISTS audit_logs_actor_id_fkey;
ALTER TABLE super_admin.login_attempts DROP CONSTRAINT IF EXISTS login_attempts_admin_id_fkey;
ALTER TABLE super_admin.admin_sessions ADD CONSTRAINT admin_sessions_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES super_admin.super_admins(id) ON DELETE CASCADE;
ALTER TABLE super_admin.audit_logs ADD CONSTRAINT audit_logs_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES super_admin.super_admins(id) ON DELETE SET NULL;
ALTER TABLE super_admin.login_attempts ADD CONSTRAINT login_attempts_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES super_admin.super_admins(id) ON DELETE SET NULL;
SQL
    say "Restore done. Row counts:"
    for t in organizations bookings chat_sessions service_units providers; do
      printf '  %-20s ' "$t"
      docker compose exec -T postgres psql -U "$PGUSER_NAME" -d "$PGDB_NAME" -tAc \
        "SELECT count(*) FROM $t" 2>/dev/null || echo "n/a"
    done
    ;;

  *)
    cat <<'EOF'
Usage:
  bash scripts/db-migrate.sh dump
  bash scripts/db-migrate.sh restore <dump-file>
EOF
    exit 1
    ;;
esac
