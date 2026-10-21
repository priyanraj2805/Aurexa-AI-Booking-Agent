#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gcp-deploy-prod.sh — deploy the Booking Agent on a GCP VM while KEEPING the
# existing named Cloudflare tunnel (dotstark.tech / api.dotstark.tech / ...).
#
#   Usage:  bash scripts/gcp-deploy-prod.sh
#
# How this differs from gcp-deploy.sh (the demo script):
#
#   1. NAMED tunnel, not quick tunnels. docker-compose.override.yml mounts
#      ./cloudflared/config.yml + credentials.json, so `docker compose` (with
#      the override auto-loaded) starts the tunnel that owns your real
#      domains. The demo script deliberately used `-f docker-compose.yml` only
#      to dodge the missing-credentials bind-mount, which would have given
#      random trycloudflare.com URLs and broken every domain + webhook.
#
#   2. No URL patching. dotstark.tech stays dotstark.tech, so
#      PUBLIC_API_BASE / FRONTEND_URL / GOOGLE_REDIRECT_URI /
#      MICROSOFT_REDIRECT_URI must NOT be rewritten — the OAuth redirect URIs
#      are registered in Google/MS consoles and would break.
#
#   3. The three quick-tunnel services (cloudflared-backend / -admin / -super)
#      are NOT started. They serve random URLs nobody uses and they double the
#      tunnel connectors.
#
#   4. The tunnel is started LAST, after the app is healthy. Until then the
#      tunnel keeps serving the old (laptop) connector.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[fail] %s\033[0m\n' "$*" >&2; exit 1; }
rand() { python3 -c "import secrets;print(secrets.token_urlsafe(48))" 2>/dev/null || head -c 48 /dev/urandom | base64 | tr -d '\n'; }

# Services we run in production (the three quick tunnels are excluded).
APP_SERVICES="postgres redis backend admin-panel backend-super super-admin gateway wa-gateway"

# ── 0. Preflight ────────────────────────────────────────────────────────────
say "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "Docker not installed. Run scripts/gcp-vm-bootstrap.sh on the VM first."
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) not available."
docker info >/dev/null 2>&1 || die "Cannot talk to Docker daemon (need: sudo usermod -aG docker \$USER, then re-login)."
echo "docker:  $(docker --version)"
echo "compose: $(docker compose version --short)"

# ── 1. Required files ──────────────────────────────────────────────────────
say "Checking required files"
for f in .env backend/.env.docker backend-super/.env.docker \
         cloudflared/config.yml cloudflared/credentials.json; do
  [ -f "$f" ] || die "Missing $f — copy it from your working machine (see the runbook)."
done
echo "all required files present"

# ── 2. Shared bridge secret (backend <-> backend-super) ───────────────────
say "Syncing INTERNAL_BRIDGE_SECRET across backend + backend-super"
BRIDGE_SECRET="$(grep -E '^INTERNAL_BRIDGE_SECRET=' .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
[ -n "$BRIDGE_SECRET" ] || BRIDGE_SECRET="$(rand)"
for f in .env backend/.env.docker backend-super/.env.docker; do
  [ -f "$f" ] || continue
  if grep -qE '^INTERNAL_BRIDGE_SECRET=' "$f"; then
    sed -i "s|^INTERNAL_BRIDGE_SECRET=.*|INTERNAL_BRIDGE_SECRET=${BRIDGE_SECRET}|" "$f"
  else
    printf '\nINTERNAL_BRIDGE_SECRET=%s\n' "$BRIDGE_SECRET" >> "$f"
  fi
done
echo "bridge secret synced (len=${#BRIDGE_SECRET})"

# ── 3. Build (only what is missing) ────────────────────────────────────────
# The app images are normally exported on the build machine and loaded here
# (scripts/gcp-images-export.ps1). Building on the VM would need 3-4 GB of
# RAM and ~25 minutes, so an already-present image is never rebuilt.
say "Checking the app images"
BUILD_LIST=""
for svc in backend admin-panel backend-super super-admin wa-gateway; do
  img="booking_agent-${svc}"
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "  $img - present"
  else
    echo "  $img - MISSING (will build)"
    BUILD_LIST="$BUILD_LIST $svc"
  fi
done
if [ -n "$BUILD_LIST" ]; then
  say "Building:$BUILD_LIST"
  docker compose build $BUILD_LIST
else
  say "All app images already present - skipping the build"
fi

# ── 4. Start data layer first ──────────────────────────────────────────────
say "Starting postgres + redis"
docker compose up -d postgres redis
for i in $(seq 1 30); do
  if docker compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    echo "postgres ready after $((i * 5))s"; break
  fi
  [ "$i" -eq 30 ] && die "postgres not ready after 2.5m — docker compose logs postgres"
  sleep 5
done

# ── 5. Start the app ───────────────────────────────────────────────────────
say "Starting application services"
docker compose up -d $APP_SERVICES

say "Waiting for the backend to answer /health"
for i in $(seq 1 90); do
  if curl -fsS http://localhost:8005/health >/dev/null 2>&1; then
    echo "backend healthy after $((i * 10))s"; break
  fi
  [ "$i" -eq 90 ] && die "backend /health not responding after 15m — docker compose logs backend"
  sleep 10
done

# ── 6. Start the named tunnel LAST ─────────────────────────────────────────
say "Starting the named Cloudflare tunnel (dotstark.tech)"
docker compose up -d cloudflared
sleep 8
docker compose logs --no-color cloudflared 2>/dev/null | tail -20 || true

# ── 7. Status ──────────────────────────────────────────────────────────────
say "Service status"
docker compose ps

cat <<'EOF'

─────────────────────────────────────────────────────────────────────────────
Live URLs (unchanged — the named tunnel keeps your domains):
  Tenant dashboard : https://dotstark.tech
  Public API base  : https://api.dotstark.tech/api/v1
  Backend health   : https://api.dotstark.tech/health
  Super admin      : https://super.dotstark.tech
  Cashfree webhook : https://api.dotstark.tech/api/v1/widget/payment/cashfree/webhook

CUTOVER REMINDER:
  Stop the tunnel connector on the OLD machine, otherwise Cloudflare
  load-balances between both and half the traffic goes to the laptop:
      (old machine)  docker compose stop cloudflared

Common commands:
  logs        docker compose logs -f backend
  restart one docker compose restart backend
  update      git pull && bash scripts/gcp-deploy-prod.sh
  db backup   docker compose exec -T postgres pg_dump -U postgres -Fc dotstark_v2 > /tmp/db.dump
─────────────────────────────────────────────────────────────────────────────
EOF
