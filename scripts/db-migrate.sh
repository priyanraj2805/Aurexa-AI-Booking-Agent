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
    docker compose exec -T postgres pg_isready -U "$PGUSER_NAME" -d "$PGDB_NAME" \
      || die "postgres is not ready"
    docker compose exec -T postgres psql -U "$PGUSER_NAME" -lqt \
      | cut -d'|' -f1 | tr -d ' ' | grep -qx "$PGDB_NAME" \
      || die "Database ${PGDB_NAME} does not exist inside the container."
    docker cp "$FILE" "$(docker compose ps -q postgres):/tmp/restore.dump"
    # --clean drops existing objects first so a re-restore is idempotent.
    docker compose exec -T postgres pg_restore -U "$PGUSER_NAME" -d "$PGDB_NAME" \
      --clean --if-exists --no-owner --no-privileges /tmp/restore.dump \
      || warn "pg_restore reported errors — check: docker compose logs postgres"
    docker compose exec -T postgres rm -f /tmp/restore.dump
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
