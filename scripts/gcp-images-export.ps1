# =============================================================
# gcp-images-export.ps1 - build the app images on YOUR LAPTOP and pack
# them for the VM.
#
#   powershell -ExecutionPolicy Bypass -File scripts/gcp-images-export.ps1
#
# Why: building on the VM needs ~3-4 GB of RAM and 25 minutes. Exporting
# the finished images (469 MB) means the VM only needs RAM to RUN them, so
# a 4 GB e2-medium is comfortable and the deploy takes ~2 minutes.
#
# Output: app-images.tar  (load it on the VM with: docker load -i app-images.tar)
# =============================================================
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host ""; Write-Host "=== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }

# The image tags compose expects. The project name is pinned to
# booking_agent so the tags match between the laptop and the VM.
$services = @("backend", "admin-panel", "backend-super", "super-admin", "wa-gateway")

Step "Building the app images"
foreach ($s in $services) {
    Write-Host "    building $s ..." -ForegroundColor Gray
    docker compose build $s
    if ($LASTEXITCODE -ne 0) { throw "build failed: $s" }
}
Ok "all images built"

Step "Exporting images"
$tags = @()
foreach ($s in $services) {
    $img = "booking_agent-$s"
    docker image inspect $img *> $null
    if ($LASTEXITCODE -ne 0) { throw "image missing after build: $img" }
    $tags += $img
}
if (Test-Path "app-images.tar") { Remove-Item "app-images.tar" -Force }
docker save -o app-images.tar $tags
if ($LASTEXITCODE -ne 0) { throw "docker save failed" }
$mb = [math]::Round((Get-Item "app-images.tar").Length / 1MB, 1)
Ok "app-images.tar  ($mb MB)  <- $($tags -join ', ')"

Write-Host ""
Write-Host "=== Done" -ForegroundColor Green
Write-Host "  The setup script uploads it automatically." -ForegroundColor Yellow
Write-Host "  To upload it by hand:" -ForegroundColor Yellow
Write-Host "    gcloud compute scp app-images.tar <VM>:/tmp/ --project=aurexa-512306 --zone=asia-south1-a --quiet" -ForegroundColor Yellow
