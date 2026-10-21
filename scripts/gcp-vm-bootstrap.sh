#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gcp-vm-bootstrap.sh — Prepare a fresh GCP Ubuntu 22.04 VM to run the
# Booking Agent. Run ONCE, right after creating the VM.
#
#   Usage:  bash scripts/gcp-vm-bootstrap.sh
#
# Installs: Docker Engine + Compose v2, creates the docker group, opens the
# firewall ports, and enables Docker on boot. Safe to re-run (idempotent).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "Updating apt packages"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl gnupg git jq ufw

say "Installing Docker Engine + Compose v2"
if ! command -v docker >/dev/null 2>&1; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
fi
sudo systemctl enable --now docker

say "Adding current user to the docker group (no sudo needed for docker)"
sudo usermod -aG docker "${USER}"

say "Opening firewall ports"
sudo ufw allow 22/tcp    || true   # SSH
sudo ufw allow 80/tcp    || true   # HTTP (nginx / cloudflared)
sudo ufw allow 443/tcp   || true   # HTTPS
sudo ufw allow 8005/tcp  || true   # Backend (direct access if needed)
sudo ufw allow 3002/tcp  || true   # Admin panel
sudo ufw allow 3003/tcp  || true   # Super admin panel
sudo ufw --force enable || true
sudo ufw status || true

say "Configuring GCP firewall (run in a SECOND terminal if this hangs)"
echo "  gcloud compute firewall-rules create allow-booking-demo \\"
echo "    --network=default --allow=tcp:80,tcp:443,tcp:8005,tcp:3002,tcp:3003 \\"
echo "    --source-ranges=0.0.0.0/0 --target-tags=booking-demo"
echo "  (make sure the VM has network tag 'booking-demo')"

say "Setting swap (2GB, so Docker builds do not OOM on e2-medium)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
fi
free -h

say "Done - now log out and back in so the docker group takes effect"
echo "  exit"
echo "  gcloud compute ssh <VM_NAME> --zone=<ZONE>"
echo "  Then: git clone <repo> && cd <repo> && bash scripts/gcp-deploy.sh"
