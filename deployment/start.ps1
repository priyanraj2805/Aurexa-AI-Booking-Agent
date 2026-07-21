docker compose up --build -d

function Get-LatestTunnelUrl {
    param(
        [string]$ServiceName,
        [int]$MaxAttempts = 15,
        [int]$SleepSeconds = 3
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $logs = docker compose logs --no-color $ServiceName 2>$null | Out-String
        $matches = [regex]::Matches($logs, 'https://[a-zA-Z0-9-]+\.trycloudflare\.com')
        if ($matches.Count -gt 0) {
            return $matches[$matches.Count - 1].Value
        }
        Start-Sleep -Seconds $SleepSeconds
    }

    return $null
}

Write-Host "Waiting for Cloudflare tunnels to publish URLs..."
$backendUrl = Get-LatestTunnelUrl -ServiceName "cloudflared-backend"
$adminUrl = Get-LatestTunnelUrl -ServiceName "cloudflared-admin"

Write-Host "========================================================="
Write-Host "YOUR PUBLIC URLS"
Write-Host "========================================================="
if ($backendUrl) {
    Write-Host "BACKEND API: $backendUrl"
} else {
    Write-Host "BACKEND API: Not found yet"
}

if ($adminUrl) {
    Write-Host "ADMIN PANEL: $adminUrl"
} else {
    Write-Host "ADMIN PANEL: Not found yet"
}

if ($backendUrl -and $adminUrl) {
    $publicApiBase = "$backendUrl/api/v1"
    $encodedBase = [uri]::EscapeDataString($publicApiBase)
    Write-Host "INTEGRATIONS (prefilled): $adminUrl/dashboard/integrations?public_api_base=$encodedBase"
    Write-Host "WIDGET SCRIPT URL: $backendUrl/static/widget/widget.js"
}
Write-Host "========================================================="

if (-not $backendUrl -or -not $adminUrl) {
    Write-Host "Tip: Re-run this command in PowerShell to fetch latest tunnel URLs:"
    Write-Host "docker compose logs --no-color cloudflared-backend cloudflared-admin | Select-String -Pattern 'https://[a-zA-Z0-9-]+\.trycloudflare\.com'"
}
