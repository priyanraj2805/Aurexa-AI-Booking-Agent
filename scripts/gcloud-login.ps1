# =============================================================
# gcloud-login.ps1 - one-time Google Cloud login on Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts/gcloud-login.ps1
#
# Optional: -Project <PROJECT_ID>   (skips the "pick a project" step)
#
# Does:
#   1. Points PATH + CLOUDSDK_PYTHON at the installed SDK
#   2. Opens the browser sign-in and waits for the verification code
#   3. Sets the active project
#   4. Prints the VMs it can see
#
# Safe to run more than once.
# =============================================================
param(
    [string]$Project = ""
)

$ErrorActionPreference = "Continue"

$sdk = "$env:USERPROFILE\gcloud-cli\google-cloud-sdk"
$bin = "$sdk\bin"
$gcloud = "$bin\gcloud.cmd"

function Step($msg) { Write-Host ""; Write-Host "=== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }

# ------------------------------------------------------------
# 1. Environment
# ------------------------------------------------------------
Step "Preparing the gcloud environment"
if (-not (Test-Path $gcloud)) {
    Write-Host "    gcloud is not installed at $gcloud" -ForegroundColor Red
    Write-Host "    Run scripts/gcp-vm-setup.ps1 first - it installs the SDK." -ForegroundColor Yellow
    exit 1
}
$env:Path = "$bin;$bin\sdk;$env:Path"

if (-not ($env:CLOUDSDK_PYTHON -and (Test-Path $env:CLOUDSDK_PYTHON))) {
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
    if (-not $py) { Write-Host "    no Python found - gcloud cannot start" -ForegroundColor Red; exit 1 }
    $env:CLOUDSDK_PYTHON = $py.Trim()
    [Environment]::SetEnvironmentVariable("CLOUDSDK_PYTHON", $env:CLOUDSDK_PYTHON, "User")
}
Ok "gcloud  : $gcloud"
Ok "python  : $env:CLOUDSDK_PYTHON"

# ------------------------------------------------------------
# 2. Auth
# ------------------------------------------------------------
$authed = (& $gcloud auth list 2>&1) -join "`n"
if ($authed -match "ACTIVE") {
    Ok "already logged in"
} else {
    Step "Signing in to Google (browser will open)"
    Write-Host "    1. A Google sign-in page opens in your browser" -ForegroundColor Yellow
    Write-Host "    2. Sign in and click Allow" -ForegroundColor Yellow
    Write-Host "    3. A verification code appears on the page - copy it" -ForegroundColor Yellow
    Write-Host "    4. Paste it here and press Enter" -ForegroundColor Yellow
    Write-Host ""
    & $gcloud auth login
    Write-Host ""
    $authed = (& $gcloud auth list 2>&1) -join "`n"
    if ($authed -notmatch "ACTIVE") {
        Write-Host "    still not logged in - try again" -ForegroundColor Red
        exit 1
    }
    Ok "logged in"
}

# ------------------------------------------------------------
# 3. Project
# ------------------------------------------------------------
Step "Selecting the project"
$cur = (& $gcloud config get-value project 2>$null)
if ($Project) {
    & $gcloud config set project $Project | Out-Null
    Ok "project set to $Project"
} elseif ($cur -and $cur -ne "(unset)") {
    Ok "project: $cur"
} else {
    Write-Host "    No project set." -ForegroundColor Yellow
    Write-Host "    Open https://console.cloud.google.com -> pick your project -> copy the Project ID" -ForegroundColor Yellow
    Write-Host "    then re-run:  powershell -File scripts/gcloud-login.ps1 -Project YOUR_PROJECT_ID" -ForegroundColor Yellow
    exit 1
}

# ------------------------------------------------------------
# 4. Show the VMs
# ------------------------------------------------------------
Step "Instances in this project"
& $gcloud compute instances list
Write-Host ""
Write-Host "  NEXT: powershell -ExecutionPolicy Bypass -File scripts/gcp-vm-setup.ps1" -ForegroundColor Cyan
