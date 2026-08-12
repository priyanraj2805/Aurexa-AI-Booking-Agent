# sync-tunnel-urls.ps1
# Quick tunnels rotate on every cloudflared restart. Run this script after any
# container restart to re-point both backends at the CURRENT live tunnel URLs:
#   .\sync-tunnel-urls.ps1            # update env files + recreate backends
#   .\sync-tunnel-urls.ps1 -NoRestart # only rewrite the env files
param([switch]$NoRestart)

$ErrorActionPreference = "Stop"

function Get-TunnelUrl([string]$container) {
  $log = (cmd /c "docker logs $container 2>&1") -join "`n"
  $matches = [regex]::Matches($log, "https://[a-z0-9-]+\.trycloudflare\.com")
  if ($matches.Count -eq 0) {
    throw "No tunnel URL found in logs of '$container'. Is it running?"
  }
  return $matches[$matches.Count - 1].Value
}

$super   = Get-TunnelUrl "booking_agent-cloudflared-super-1"
$admin   = Get-TunnelUrl "booking_agent-cloudflared-admin-1"
$backend = Get-TunnelUrl "booking_agent-cloudflared-backend-1"

Write-Host ""
Write-Host "Current live tunnel URLs:"
Write-Host "  SUPER   = $super"
Write-Host "  ADMIN   = $admin"
Write-Host "  BACKEND = $backend"
Write-Host ""

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$superEnv = Join-Path $root "backend-super\.env.docker"
$backendEnv = Join-Path $root "backend\.env.docker"

function Set-EnvValue([string]$file, [string]$key, [string]$value) {
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  $content = [System.IO.File]::ReadAllText($file)
  $pattern = "(?m)^$([regex]::Escape($key))=.*$"
  $replacement = "$key=$value"
  if (-not [regex]::IsMatch($content, $pattern)) {
    throw "Key '$key' not found in $file"
  }
  $updated = [regex]::Replace($content, $pattern, $replacement.Replace('$', '$$'))
  [System.IO.File]::WriteAllText($file, $updated, $utf8)
}

# backend-super: consent links + frontend URLs
Set-EnvValue $superEnv "FRONTEND_URL" $super
Set-EnvValue $superEnv "TENANT_CONSOLE_URL" $admin
Set-EnvValue $superEnv "IMPERSONATION_CONSENT_BASE_URL" $super

# backend: dashboard URLs, widget/API base, OAuth + Zoom callbacks
Set-EnvValue $backendEnv "FRONTEND_URL" $admin
Set-EnvValue $backendEnv "FRONTEND_BASE_URL" $admin
Set-EnvValue $backendEnv "PUBLIC_API_BASE" "$backend/api/v1"
Set-EnvValue $backendEnv "GOOGLE_REDIRECT_URI" "$backend/api/v1/dashboard/google-calendar/callback"
Set-EnvValue $backendEnv "MICROSOFT_REDIRECT_URI" "$backend/api/v1/dashboard/outlook-calendar/callback"
Set-EnvValue $backendEnv "ZOOM_REDIRECT_URI" "$backend/api/v1/dashboard/meeting-providers/zoom/callback"

Write-Host "Env files updated."

if (-not $NoRestart) {
  Write-Host "Recreating backend + backend-super containers..."
  docker compose up -d backend backend-super
  Write-Host "Done. Emailed consent links will now point at the live tunnels."
} else {
  Write-Host "Skipped container restart. Run 'docker compose up -d backend backend-super' manually to apply."
}
