#!/usr/bin/env bash
# =============================================================
# gcp-vm-setup.sh - runs ON THE GCP VM, does the whole server side.
#
#   bash /tmp/gcp-vm-setup.sh
#
# Idempotent: safe to re-run (it re-clones only when missing, restores the
# DB only when a dump is present, and the deploy script is re-runnable).
#
# Expects these in /tmp (uploaded by gcp-vm-setup.ps1):
#   /tmp/gcp-vm-bootstrap.sh
#   /tmp/secrets.env /tmp/backend.env.docker /tmp/backend-super.env.docker
#   /tmp/credentials.json
#   /tmp/db-dump-*.dump
#   /tmp/wa-auth.tar.gz
# =============================================================
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[warn] %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m[fail] %s\033[0m\n' "$*" >&2; exit 1; }

REPO_DIR="$HOME/BookingChatBot"
BRANCH="Aurexa-production"

# ------------------------------------------------------------
# 1. Bootstrap (docker, compose, firewall, swap)
# ------------------------------------------------------------
say "Bootstrapping the VM (docker + compose + swap)"
if ! command -v docker >/dev/null 2>&1; then
  [ -f /tmp/gcp-vm-bootstrap.sh ] || die "/tmp/gcp-vm-bootstrap.sh missing"
  sudo bash /tmp/gcp-vm-bootstrap.sh
  say "Docker installed. Re-run this script after 'newgrp docker' (or log out/in)."
  exit 0
fi
docker info >/dev/null 2>&1 || die "Docker daemon not reachable as $USER. Run: newgrp docker   (then re-run this script)"
echo "docker: $(docker --version)"

# ------------------------------------------------------------
# 2. Clone / update the code
# ------------------------------------------------------------
say "Fetching the code ($BRANCH)"
# The repository is PRIVATE, so a bare `git clone` on the VM needs GitHub
# credentials. The laptop already has them, so the committed tree is
# exported and uploaded instead - no token ever lands on the VM.
if [ -f /tmp/repo.tar.gz ]; then
  say "Unpacking the code from /tmp/repo.tar.gz"
  rm -rf "$REPO_DIR"
  mkdir -p "$REPO_DIR"
  tar -xzf /tmp/repo.tar.gz -C "$REPO_DIR"
  rm -f /tmp/repo.tar.gz
elif [ ! -d "$REPO_DIR/.git" ]; then
  git clone https://github.com/ManojDS17/BookingChatBot.git "$REPO_DIR"
fi
cd "$REPO_DIR"
# If a .git came along (or the clone worked), try to move forward. A
# failure here is not fatal - the tree on disk is what runs.
if [ -d .git ]; then
  git fetch --all --tags 2>/dev/null || warn "git fetch failed (no credentials?) - using the uploaded tree"
  git checkout "$BRANCH" 2>/dev/null || true
  git pull --ff-only origin "$BRANCH" 2>/dev/null || warn "git pull failed - using the uploaded tree"
  git log --oneline -1 || true
else
  echo "  (no git metadata - running the uploaded tree)"
fi
ls -1 | head -12

# ------------------------------------------------------------
# 3. Secrets (gitignored - they must be uploaded)
# ------------------------------------------------------------
say "Installing the secret files"
install_secret() {
  local src="$1" dst="$2"
  if [ -f "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chmod 600 "$dst"
    echo "  installed $dst"
  else
    warn "$src not found - $dst left as-is"
  fi
}
install_secret /tmp/secrets.env                    "$REPO_DIR/.env"
install_secret /tmp/backend.env.docker             "$REPO_DIR/backend/.env.docker"
install_secret /tmp/backend-super.env.docker       "$REPO_DIR/backend-super/.env.docker"
install_secret /tmp/credentials.json               "$REPO_DIR/cloudflared/credentials.json"
chmod 600 "$REPO_DIR/.env" "$REPO_DIR/backend/.env.docker" \
          "$REPO_DIR/backend-super/.env.docker" \
          "$REPO_DIR/cloudflared/credentials.json" 2>/dev/null || true

# Pin the compose project name. Without it the project name is derived from
# the directory name (BookingChatBot -> bookingagent) and the image tags
# would not match the ones exported from the laptop (booking_agent-*).
if ! grep -q '^COMPOSE_PROJECT_NAME=' "$REPO_DIR/.env" 2>/dev/null; then
  printf '\nCOMPOSE_PROJECT_NAME=booking_agent\n' >> "$REPO_DIR/.env"
fi
grep '^COMPOSE_PROJECT_NAME=' "$REPO_DIR/.env" || true

# ------------------------------------------------------------
# 4. Fix the stale Zoom redirect (points at an old quick-tunnel URL)
# ------------------------------------------------------------
say "Pointing ZOOM_REDIRECT_URI at the real domain"
if grep -q '^ZOOM_REDIRECT_URI=' "$REPO_DIR/backend/.env.docker" 2>/dev/null; then
  sed -i 's|^ZOOM_REDIRECT_URI=.*|ZOOM_REDIRECT_URI=https://api.dotstark.tech/api/v1/dashboard/meeting-providers/zoom/callback|' \
    "$REPO_DIR/backend/.env.docker"
  grep '^ZOOM_REDIRECT_URI=' "$REPO_DIR/backend/.env.docker"
fi

# ------------------------------------------------------------
# 5. App images (exported on the build machine - no build on the VM)
# ------------------------------------------------------------
if [ -f /tmp/app-images.tar ]; then
  say "Loading the app images from /tmp/app-images.tar"
  docker load -i /tmp/app-images.tar
  docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep booking_agent || true
  rm -f /tmp/app-images.tar
else
  warn "no /tmp/app-images.tar - the VM will build the images (slower, needs RAM)"
fi

# ------------------------------------------------------------
# 6. Database
# ------------------------------------------------------------
DUMP="$(ls -1 /tmp/db-dump-*.dump 2>/dev/null | head -1 || true)"
if [ -n "$DUMP" ]; then
  say "Restoring the database from $DUMP"
  bash scripts/db-migrate.sh restore "$DUMP"
else
  warn "no /tmp/db-dump-*.dump found - keeping the database as it is"
fi

# ------------------------------------------------------------
# 7. WhatsApp (Baileys) session
# ------------------------------------------------------------
if [ -f /tmp/wa-auth.tar.gz ]; then
  say "Restoring the WhatsApp session"
  docker compose up -d wa-gateway
  docker run --rm -v booking_agent_wa_auth:/data -v /tmp:/in alpine \
    sh -c "cd /data && tar xzf /in/wa-auth.tar.gz"
  docker compose restart wa-gateway
  sleep 5
  docker compose logs --tail 20 wa-gateway || true
else
  warn "no /tmp/wa-auth.tar.gz found - link WhatsApp again from the admin panel"
fi

# ------------------------------------------------------------
# 8. Build + start (named tunnel, no quick tunnels)
# ------------------------------------------------------------
say "Building and starting the app (this takes 15-25 min the first time)"
bash scripts/gcp-deploy-prod.sh

say "Done. Check the URLs printed above, then do the cutover from your laptop:"
echo "  docker compose stop cloudflared"
