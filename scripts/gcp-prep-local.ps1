# ============================================================
# gcp-prep-local.ps1 - Phase 0 (runs on YOUR LAPTOP, before touching the VM)
#
#   powershell -ExecutionPolicy Bypass -File scripts/gcp-prep-local.ps1
#
# Does three things and nothing else:
#   1. Dumps Postgres into a local .dump file (binary-safe: the dump is
#      written INSIDE the container and copied out with `docker cp`.
#      Redirecting pg_dump with ">" corrupts the archive under PowerShell.)
#   2. Packs the Baileys WhatsApp session (booking_agent_wa_auth volume).
#   3. Packs the gitignored secret files the VM needs but git will not carry.
#
# Outputs (all in the repo root):
#   db-dump-<ts>.dump
#   wa-auth.tar.gz
#   deploy-secrets/  (.env, backend.env.docker, backend-super.env.docker,
#                     credentials.json, config.yml)
#
# NOTE: this file is deliberately plain ASCII. PowerShell 5.1 reads .ps1 as
# ANSI when there is no BOM, so em-dashes / smart quotes silently corrupt it.
# ============================================================
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "" ; Write-Host "=== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }

# ------------------------------------------------------------
# 0. Preflight
# ------------------------------------------------------------
Step "Preflight"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found on PATH"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "cannot talk to the docker daemon - is Docker Desktop running?" }

$pgContainer = (docker compose ps -q postgres)
if (-not $pgContainer) {
    docker compose up -d postgres | Out-Null
    Start-Sleep -Seconds 8
    $pgContainer = (docker compose ps -q postgres)
}
if (-not $pgContainer) { throw "postgres container not found (docker compose ps -q postgres)" }
Ok "postgres container: $pgContainer"

# POSTGRES_* come from the root .env (the same file compose reads).
$envMap = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*#' -or -not $_.Trim()) { return }
    $p = $_ -split '=', 2
    if ($p.Count -eq 2) { $envMap[$p[0].Trim()] = $p[1].Trim() }
}
$PGUser = "postgres"
$PGDb   = "dotstark_v2"
if ($envMap.ContainsKey("POSTGRES_USER")) { $PGUser = $envMap["POSTGRES_USER"] }
if ($envMap.ContainsKey("POSTGRES_DB"))   { $PGDb   = $envMap["POSTGRES_DB"] }
Ok "database: $PGDb (user $PGUser)"

# ------------------------------------------------------------
# 1. DB dump
# ------------------------------------------------------------
Step "Dumping the database"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dump  = "db-dump-$stamp.dump"
docker compose exec -T postgres pg_dump -U $PGUser -Fc -f /tmp/prep.dump $PGDb
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
docker cp "${pgContainer}:/tmp/prep.dump" "./$dump"
docker compose exec -T postgres rm -f /tmp/prep.dump
if (-not (Test-Path $dump)) { throw "dump file was not created" }
$dumpMb = [math]::Round((Get-Item $dump).Length / 1MB, 2)
Ok "$dump  ($dumpMb MB)"

# ------------------------------------------------------------
# 2. WhatsApp session
# ------------------------------------------------------------
Step "Packing the WhatsApp (Baileys) session"
docker run --rm -v booking_agent_wa_auth:/data -v "${PWD}:/out" alpine tar czf /out/wa-auth.tar.gz -C /data .
if (-not (Test-Path "wa-auth.tar.gz")) { throw "wa-auth.tar.gz was not created" }
$waKb = [math]::Round((Get-Item "wa-auth.tar.gz").Length / 1KB, 1)
Ok "wa-auth.tar.gz  ($waKb KB)"

# ------------------------------------------------------------
# 3. Secrets
# ------------------------------------------------------------
Step "Packing the secret files (gitignored - git will not carry these)"
$sec = Join-Path $Root "deploy-secrets"
New-Item -ItemType Directory -Force -Path $sec | Out-Null

$files = @{
    ".env"                         = ".env"
    "backend/.env.docker"          = "backend.env.docker"
    "backend-super/.env.docker"    = "backend-super.env.docker"
    "cloudflared/credentials.json" = "credentials.json"
}
foreach ($src in $files.Keys) {
    $from = Join-Path $Root $src
    if (-not (Test-Path $from)) { throw "missing $src - cannot deploy without it" }
    Copy-Item $from (Join-Path $sec $files[$src]) -Force
    Ok "copied $src"
}

# cloudflared/config.yml IS tracked by git (the VM gets it from the clone),
# but copy it too so the pack is self-contained.
Copy-Item (Join-Path $Root "cloudflared/config.yml") (Join-Path $sec "config.yml") -Force
Ok "copied cloudflared/config.yml (also in git, harmless)"

Write-Host ""
Write-Host "=== Done" -ForegroundColor Cyan
Write-Host "    $dump"
Write-Host "    wa-auth.tar.gz"
Write-Host "    deploy-secrets\"
Write-Host ""
Write-Host "  NEXT: create the VM, then copy these up with gcloud compute scp." -ForegroundColor Yellow
Write-Host "  Runbook: docs/GCP-LIVE-DEPLOY.md" -ForegroundColor Yellow
