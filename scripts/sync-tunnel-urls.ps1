# sync-tunnel-urls.ps1
# Quick-tunnel URL re-sync. Run AFTER `docker compose up` (tunnels must be up).
# Reads the live trycloudflare URLs from the cloudflared container logs and
# rewrites the public-URL keys in backend/.env.docker and backend-super/.env.docker,
# then recreates the two backends so the new values take effect.
#
# Note: consent links / OAuth redirects emailed BEFORE a tunnel restart are
# permanently dead. After running this, send a fresh impersonation request.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/sync-tunnel-urls.ps1

param(
  [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $root "docker-compose.yml"
$backendEnv = Join-Path $root "backend\.env.docker"
$superEnv = Join-Path $root "backend-super\.env.docker"

function Get-TunnelUrl([string]$service) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $logs = & docker compose -f $composeFile logs $service 2>$null | Out-String
    $m = [regex]::Match($logs, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($m.Success) { return $m.Value }
    Start-Sleep -Seconds 5
  }
  throw "Tunnel URL for '$service' not found within ${TimeoutSeconds}s -- is it running?"
}

function Set-EnvValue([string]$path, [string]$key, [string]$value) {
  $lines = Get-Content -LiteralPath $path
  $found = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^$([regex]::Escape($key))=") {
      $lines[$i] = "$key=$value"
      $found = $true
    }
  }
  if (-not $found) { throw "Key '$key' not found in $path" }
  Set-Content -LiteralPath $path -Value $lines -Encoding ascii
  Write-Host "    $key = $value"
}

Write-Host "Reading live tunnel URLs (timeout ${TimeoutSeconds}s) ..."
$backendTunnel = Get-TunnelUrl "cloudflared-backend"
$adminTunnel = Get-TunnelUrl "cloudflared-admin"
$superTunnel = Get-TunnelUrl "cloudflared-super"
Write-Host ""
Write-Host "  backend(8005): $backendTunnel"
Write-Host "  admin(3002):   $adminTunnel"
Write-Host "  super(3003):   $superTunnel"
Write-Host ""

Write-Host "Syncing backend\.env.docker ..."
Set-EnvValue $backendEnv "GOOGLE_REDIRECT_URI" "$backendTunnel/api/v1/dashboard/google-calendar/callback"
Set-EnvValue $backendEnv "MICROSOFT_REDIRECT_URI" "$backendTunnel/api/v1/dashboard/outlook-calendar/callback"
Set-EnvValue $backendEnv "ZOOM_REDIRECT_URI" "$backendTunnel/api/v1/dashboard/meeting-providers/zoom/callback"
Set-EnvValue $backendEnv "FRONTEND_URL" $adminTunnel
Set-EnvValue $backendEnv "FRONTEND_BASE_URL" $adminTunnel
Set-EnvValue $backendEnv "PUBLIC_API_BASE" "$backendTunnel/api/v1"

Write-Host "Syncing backend-super\.env.docker ..."
Set-EnvValue $superEnv "FRONTEND_URL" $superTunnel
Set-EnvValue $superEnv "TENANT_CONSOLE_URL" $adminTunnel
Set-EnvValue $superEnv "IMPERSONATION_CONSENT_BASE_URL" $superTunnel

Write-Host ""
Write-Host "Recreating backend + backend-super with the new URLs ..."
& docker compose -f $composeFile up -d --force-recreate backend backend-super 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

Write-Host ""
Write-Host "Verifying ..."
Start-Sleep -Seconds 8
& docker compose -f $composeFile exec -T backend-super python -c "from app.core.config import settings; print('consent base =', settings.IMPERSONATION_CONSENT_BASE_URL); print('console     =', settings.TENANT_CONSOLE_URL); print('frontend    =', settings.FRONTEND_URL)"
Write-Host ""
Write-Host "Done. Send a fresh impersonation request -- old emailed links are dead."