# =============================================================
# gcp-vm-setup.ps1 - runs on YOUR LAPTOP. One command that takes the
# project from "VM created" to "app running on the VM".
#
#   powershell -ExecutionPolicy Bypass -File scripts/gcp-vm-setup.ps1
#
# Optional args (auto-detected when omitted):
#   -VM <name>       e.g. dotstark-prod
#   -Zone <zone>     e.g. asia-south1-a
#   -Project <id>    GCP project id
#
# What it does:
#   1. Installs the gcloud CLI if it is missing (no admin needed)
#   2. Auto-detects the VM (name + zone) from the project
#   3. Uploads the bootstrap script, the secret files, the DB dump and the
#      WhatsApp session to the VM
#   4. Runs scripts/gcp-vm-setup.sh on the VM (clone, secrets, DB restore,
#      WhatsApp session, build, start)
#
# NOTE: plain ASCII only - PowerShell 5.1 reads .ps1 as ANSI without a BOM.
# =============================================================
param(
    [string]$VM = "",
    [string]$Zone = "",
    [string]$Project = ""
)

# Native commands (gcloud) write their progress and "not logged in" notices
# to STDERR. With ErrorActionPreference = "Stop" that becomes a terminating
# NativeCommandError and kills the script on a perfectly normal state. We
# check $LASTEXITCODE ourselves, and explicit `throw` still terminates.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host ""; Write-Host "=== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# ------------------------------------------------------------
# 1. gcloud CLI
# ------------------------------------------------------------
Step "Checking the gcloud CLI"

function Resolve-Gcloud {
    # gcloud.cmd first, ALWAYS. The SDK folder also ships gcloud.ps1 (a
    # PowerShell wrapper); Get-Command gcloud can return that one, and
    # PowerShell cannot execute it when the execution policy blocks scripts.
    $candidates = @(
        "$env:USERPROFILE\gcloud-cli\google-cloud-sdk\bin\gcloud.cmd",
        "$env:LOCALAPPDATA\Google\Cloud SDK\bin\gcloud.cmd",
        "C:\Program Files (x86)\Google\Cloud SDK\bin\gcloud.cmd",
        "C:\Program Files\Google\Cloud SDK\bin\gcloud.cmd"
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    foreach ($n in @("gcloud.cmd", "gcloud.exe")) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}

# The Windows SDK zip ships without a bundled Python, so gcloud needs
# CLOUDSDK_PYTHON. Find one and set it if it is not already set.
function Ensure-GcloudPython {
    if ($env:CLOUDSDK_PYTHON -and (Test-Path $env:CLOUDSDK_PYTHON)) { return }
    $py = $null
    try {
        $out = & py -c "import sys; print(sys.executable)" 2>$null
        $py = ($out | Where-Object { $_ -match "python\.exe$" } | Select-Object -First 1)
    } catch {}
    if (-not $py) {
        foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
                         "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                         "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe")) {
            if (Test-Path $p) { $py = $p; break }
        }
    }
    if ($py) {
        $env:CLOUDSDK_PYTHON = $py.Trim()
        Ok "CLOUDSDK_PYTHON = $($env:CLOUDSDK_PYTHON)"
    }
}

$gcloud = Resolve-Gcloud
if (-not $gcloud) {
    Write-Host "    gcloud not found - installing the Google Cloud CLI..." -ForegroundColor Yellow
    $zip = "$env:USERPROFILE\gcloud.zip"
    $dest = "$env:USERPROFILE\gcloud-cli"
    Invoke-WebRequest "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64.zip" -OutFile $zip
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    # tar extracts ~20x faster than Expand-Archive on this machine
    tar -xf $zip -C $dest
    $gcloud = Resolve-Gcloud
    if (-not $gcloud) { throw "gcloud install failed" }
    $bin = Split-Path -Parent $gcloud
    $env:Path = "$bin;$env:Path"
    [Environment]::SetEnvironmentVariable("Path", "$bin;" + [Environment]::GetEnvironmentVariable("Path", "User"), "User")
    Ok "gcloud installed at $gcloud"
} else {
    Ok "gcloud: $gcloud"
}
Ensure-GcloudPython

# Sanity: gcloud must actually run before we go further.
$gcloudVer = (& $gcloud version 2>&1) -join "`n"
if ($gcloudVer -notmatch "Google Cloud SDK") {
    Write-Host "    gcloud does not run:" -ForegroundColor Red
    Write-Host "    $gcloudVer" -ForegroundColor Red
    Write-Host "    CLOUDSDK_PYTHON=$env:CLOUDSDK_PYTHON" -ForegroundColor Yellow
    exit 1
}
Ok "gcloud works"

# ------------------------------------------------------------
# 2. Auth + project
# ------------------------------------------------------------
Step "Checking the gcloud login"
$authList = (& $gcloud auth list 2>&1) -join "`n"
if ($authList -notmatch "ACTIVE") {
    Write-Host ""
    Write-Host "    Not logged in yet - signing in now." -ForegroundColor Yellow
    Write-Host "      1. A Google sign-in page will open in your browser" -ForegroundColor Yellow
    Write-Host "      2. Sign in with the account that owns project aurexa-512306" -ForegroundColor Yellow
    Write-Host "      3. Click ALLOW" -ForegroundColor Yellow
    Write-Host "      4. Copy the verification code and paste it below" -ForegroundColor Yellow
    Write-Host ""
    & $gcloud auth login
    Write-Host ""
    $authList = (& $gcloud auth list 2>&1) -join "`n"
    if ($authList -notmatch "ACTIVE") {
        Write-Host "    Still not logged in. Try: $gcloud auth login" -ForegroundColor Red
        exit 1
    }
}
Ok "logged in"

if ($Project) { & $gcloud config set project $Project | Out-Null }
$curProject = (& $gcloud config get-value project 2>$null)
if ($curProject -eq "(unset)") {
    Write-Host "  No project set. Run: $gcloud init   (or pass -Project <id>)" -ForegroundColor Yellow
    exit 1
}
Ok "project: $curProject"

# ------------------------------------------------------------
# 3. Find the VM
# ------------------------------------------------------------
Step "Finding the VM"
$listRaw = (& $gcloud compute instances list --format "value(name,zone)" 2>&1) -join "`n"
$instances = @()
foreach ($line in ($listRaw -split "`n")) {
    if ($line.Trim() -and $line -notmatch "ERROR|WARNING") {
        $parts = $line -split "\s+"
        if ($parts.Count -ge 2) {
            $z = ($parts[1] -replace "/instances", "")
            $instances += [pscustomobject]@{ Name = $parts[0]; Zone = $z }
        }
    }
}
if ($instances.Count -eq 0) {
    Write-Host "  No instances found in project $curProject." -ForegroundColor Yellow
    Write-Host "  Create the VM first, then re-run this script." -ForegroundColor Yellow
    exit 1
}
if ($VM -and $Zone) {
    $target = $instances | Where-Object { $_.Name -eq $VM -and $_.Zone -eq $Zone } | Select-Object -First 1
} elseif ($VM) {
    $target = $instances | Where-Object { $_.Name -eq $VM } | Select-Object -First 1
} else {
    $target = $instances | Select-Object -First 1
}
if (-not $target) {
    Write-Host "  VM '$VM' not found. Available:" -ForegroundColor Yellow
    $instances | ForEach-Object { Write-Host "      $($_.Name)  ($($_.Zone))" -ForegroundColor Yellow }
    exit 1
}
Ok "VM: $($target.Name)  zone: $($target.Zone)"

# ------------------------------------------------------------
# 4. Upload everything
# ------------------------------------------------------------
Step "Uploading files to the VM"
# scp takes the instance inside the destination (INSTANCE:/path), so it must
# NOT also get an instance positional - it rejects that as "unrecognized
# arguments". ssh takes the instance as its own positional.
$commonFlags = @("--zone", $target.Zone, "--project", $curProject, "--quiet")

function Push([string]$local, [string]$remote) {
    Write-Host "    $local  ->  $remote"
    & $gcloud compute scp $local "$($target.Name):$remote" @commonFlags
    if ($LASTEXITCODE -ne 0) { throw "scp failed: $local" }
}

Push "scripts/gcp-vm-bootstrap.sh"  "/tmp/gcp-vm-bootstrap.sh"
Push "scripts/gcp-vm-setup.sh"      "/tmp/gcp-vm-setup.sh"
Push "deploy-secrets/.env"                      "/tmp/secrets.env"
Push "deploy-secrets/backend.env.docker"        "/tmp/backend.env.docker"
Push "deploy-secrets/backend-super.env.docker"  "/tmp/backend-super.env.docker"
Push "deploy-secrets/credentials.json"          "/tmp/credentials.json"

$dump = Get-ChildItem "db-dump-*.dump" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($dump) { Push $dump.Name "/tmp/$($dump.Name)" } else { Warn "no db-dump-*.dump found (Phase 0 not run?)" }
if (Test-Path "wa-auth.tar.gz") { Push "wa-auth.tar.gz" "/tmp/wa-auth.tar.gz" } else { Warn "no wa-auth.tar.gz found" }
if (Test-Path "app-images.tar") { Push "app-images.tar" "/tmp/app-images.tar" } else { Warn "no app-images.tar found - the VM will build (slower, needs RAM). Run scripts/gcp-images-export.ps1" }

# ------------------------------------------------------------
# 5. Run the setup on the VM
# ------------------------------------------------------------
Step "Running the setup on the VM (clone, secrets, DB restore, build, start)"
Write-Host "    first run installs Docker and builds the images - this takes 15-25 min." -ForegroundColor Yellow
Write-Host ""
& $gcloud compute ssh $target.Name @commonFlags --command "bash /tmp/gcp-vm-setup.sh"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  The remote script exited with $LASTEXITCODE." -ForegroundColor Yellow
    Write-Host "  Re-run it after 'newgrp docker':" -ForegroundColor Yellow
    Write-Host "      $gcloud compute ssh $target.Name @commonFlags --command `"newgrp docker`"" -ForegroundColor Yellow
    Write-Host "      $gcloud compute ssh $target.Name @commonFlags --command `"bash /tmp/gcp-vm-setup.sh`"" -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "=== DONE" -ForegroundColor Green
Write-Host "  Verify:  https://api.dotstark.tech/health"
Write-Host "  Then CUTOVER from your laptop:  docker compose stop cloudflared"
Write-Host "  (until you do that, traffic still goes to the laptop)"
