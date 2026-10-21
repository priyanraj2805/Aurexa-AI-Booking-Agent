#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gcp-deploy.sh — One-shot deploy of the Booking Agent on a GCP Ubuntu VM.
#
#   Usage:  bash scripts/gcp-deploy.sh
#
# Why this script exists (the 3 traps that break a naive `docker compose up`):
#
#   1. docker-compose.override.yml mounts ./cloudflared/credentials.json,
#      which is GITIGNORED (*.json + credentials.json in .gitignore). On a fresh
#      clone that file does not exist and Docker fails to bind-mount it.
#      → We run with `-f docker-compose.yml` ONLY, which skips the override.
#      → Quick tunnels (random trycloudflare.com URLs) then give public URLs.
#
#   2. The backend reads secrets from backend/.env.docker (gitignored) and
#      compose reads POSTGRES_* from the root .env (gitignored). Both must be
#      created from their .example templates.
#      → We scaffold them if missing and STOP so you can paste real secrets.
#
#   3. backend-super and backend must share INTERNAL_BRIDGE_SECRET.
#      → We generate one value and write it into BOTH files.
#
#   4. Google/Microsoft OAuth redirect URIs must point at the PUBLIC tunnel URL,
#      and quick-tunnel URLs rotate on every restart. We patch them after the
#      tunnel URL is known and print a warning to re-run after any restart.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE_FILE="docker-compose.yml"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[fail] %s\033[0m\n' "$*" >&2; exit 1; }
rand() { python3 -c "import secrets;print(secrets.token_urlsafe(48))" 2>/dev/null || head -c 48 /dev/urandom | base64 | tr -d '\n'; }

# ── 0. Sanity checks ────────────────────────────────────────────────────────
say "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "Docker not installed. Run: sudo apt update && sudo apt install -y docker.io && sudo systemctl start docker"
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) not available."
docker info >/dev/null 2>&1 || die "Cannot talk to Docker daemon. Run: sudo systemctl start docker && sudo usermod -aG docker \$USER (then re-login)."
echo "docker:  $(docker --version)"
echo "compose: $(docker compose version --short)"

# ── 1. Scaffold env files (idempotent) ──────────────────────────────────────
say "Scaffolding env files (never overwrites an existing file)"
[ -f .env ]                         || { cp .env.example .env; echo "created .env"; }
[ -f backend/.env.docker ]         || { cp backend/.env.docker.example backend/.env.docker; echo "created backend/.env.docker"; }
[ -f backend-super/.env.docker ]   || { cp backend-super/.env.docker.example backend-super/.env.docker; echo "created backend-super/.env.docker"; }
mkdir -p cloudflared

# ── 2. Shared bridge secret between backend and backend-super ───────────────
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

# ── 3. Rotate the dev-default JWT secret ───────────────────────────────────
say "Ensuring a non-default JWT_SECRET_KEY"
DEV_JWT="dev-only-change-me-in-production-dotstark-2026-booking-agent"
if grep -qF "JWT_SECRET_KEY=${DEV_JWT}" backend/.env.docker; then
  NEW_JWT="$(rand)"
  sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=${NEW_JWT}|" backend/.env.docker
  echo "rotated JWT_SECRET_KEY"
else
  echo "JWT_SECRET_KEY already set - left untouched"
fi


# ── 4. Build + start ────────────────────────────────────────────────────────
say "Building images (several minutes on first run)"
docker compose -f "$COMPOSE_FILE" build

say "Starting all services"
docker compose -f "$COMPOSE_FILE" up -d

say "Waiting for backend to become healthy"
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8005/health >/dev/null 2>&1; then
    echo "backend healthy after $((i * 10))s"
    break
  fi
  [ "$i" -eq 60 ] && warn "backend /health not responding after 10m - check: docker compose -f $COMPOSE_FILE logs backend"
  sleep 10
done

# ── 5. Discover tunnel URLs ─────────────────────────────────────────────────
say "Current public tunnel URLs"
docker compose -f "$COMPOSE_FILE" logs --no-color cloudflared-backend cloudflared-admin cloudflared-super 2>/dev/null \
  | grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' | sort -u | tee /tmp/booking-tunnel-urls.txt || true

BACKEND_URL="$(grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' /tmp/booking-tunnel-urls.txt 2>/dev/null | head -1 || true)"

# ── 6. Patch public-facing URLs to the tunnel ───────────────────────────────
if [ -n "$BACKEND_URL" ]; then
  say "Patching public URLs to ${BACKEND_URL}"
  for f in .env backend/.env.docker backend-super/.env.docker; do
    [ -f "$f" ] || continue
    sed -i "s|^PUBLIC_API_BASE=.*|PUBLIC_API_BASE=${BACKEND_URL}/api/v1|" "$f"
    sed -i "s|^FRONTEND_URL=.*|FRONTEND_URL=${BACKEND_URL}|" "$f"
    sed -i "s|^FRONTEND_BASE_URL=.*|FRONTEND_BASE_URL=${BACKEND_URL}|" "$f"
    sed -i "s|^GOOGLE_REDIRECT_URI=.*|GOOGLE_REDIRECT_URI=${BACKEND_URL}/api/v1/dashboard/google-calendar/callback|" "$f"
    sed -i "s|^MICROSOFT_REDIRECT_URI=.*|MICROSOFT_REDIRECT_URI=${BACKEND_URL}/api/v1/dashboard/outlook-calendar/callback|" "$f"
  done
  say "Restarting backend to pick up patched URLs"
  docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend backend-super
  echo
  echo "  Widget embed snippet:"
  echo "  <script src=\"${BACKEND_URL}/api/v1/widget/static/widget.js\"></script>"
  echo "  Health:  ${BACKEND_URL}/health"
  echo "  OpenAPI: ${BACKEND_URL}/docs"
else
  warn "No tunnel URL detected yet. Re-run: docker compose -f $COMPOSE_FILE logs cloudflared-backend | grep trycloudflare"
fi

# ── 7. Status ───────────────────────────────────────────────────────────────
say "Service status"
docker compose -f "$COMPOSE_FILE" ps

cat <<'EOF'

─────────────────────────────────────────────────────────────────────────────
Next steps:
  1. Open the backend tunnel URL /docs  -> confirm Swagger is live
  2. Put real secrets into .env + backend/.env.docker BEFORE the demo:
       LLM_API_KEY, SMTP_*, RAZORPAY_*, WA_GATEWAY_SERVICE_KEY
  3. Quick-tunnel URLs ROTATE on restart. For a stable demo URL, set named
     tunnel tokens in .env:
       CLOUDFLARE_TUNNEL_TOKEN_BACKEND=
       CLOUDFLARE_TUNNEL_TOKEN_ADMIN=
     then switch the cloudflared-* services to `tunnel run --token ...`
  4. Re-run this script after any restart to re-patch the OAuth redirect URIs.
─────────────────────────────────────────────────────────────────────────────
EOF
