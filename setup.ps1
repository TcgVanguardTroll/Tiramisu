# Tiramisu install script for Windows
# Usage:  .\setup.ps1
# Idempotent: safe to re-run after a `git pull` to update dependencies.

$ErrorActionPreference = "Stop"
$INSTALL_DIR = $PSScriptRoot

Write-Host ""
Write-Host "Installing Tiramisu from: $INSTALL_DIR" -ForegroundColor Cyan
Write-Host ""

# 1. Find Python
$python = $null
$candidates = @(
    "C:\Python314\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $python = $c; break }
}
if (-not $python) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Write-Host "  [ERROR] Python not found." -ForegroundColor Red
    Write-Host "  Install Python 3.10+ from https://python.org and retry."
    exit 1
}
Write-Host "  [1/5] Python: $python" -ForegroundColor Green

# 2. Install dependencies
Write-Host "  [2/5] Installing dependencies..."
& $python -m pip install --quiet --disable-pip-version-check -r "$INSTALL_DIR\requirements.txt"
Write-Host "        Done (anthropic, rich, prompt_toolkit)." -ForegroundColor Green

# 3. Add to PATH if not already there
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$needsRestart = $false
if ($userPath -notlike "*$INSTALL_DIR*") {
    [System.Environment]::SetEnvironmentVariable("PATH", "$userPath;$INSTALL_DIR", "User")
    Write-Host "  [3/5] Added to user PATH." -ForegroundColor Green
    $needsRestart = $true
} else {
    Write-Host "  [3/5] PATH already includes: $INSTALL_DIR" -ForegroundColor Green
}

# 4. Ensure .env exists with a key placeholder
$envDir  = "$HOME\.tiramisu"
$envFile = "$envDir\.env"
$needsKey = $false
if (-not (Test-Path $envFile)) {
    New-Item -ItemType Directory -Force $envDir | Out-Null
    @"
# Tiramisu environment
# Get your key at: https://console.anthropic.com/
ANTHROPIC_API_KEY=
"@ | Out-File -Encoding utf8 $envFile
    Write-Host "  [4/5] Created: $envFile" -ForegroundColor Yellow
    $needsKey = $true
} else {
    $content = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
    if ($content -notmatch "ANTHROPIC_API_KEY=\S") {
        Write-Host "  [4/5] .env exists but no API key set: $envFile" -ForegroundColor Yellow
        $needsKey = $true
    } else {
        Write-Host "  [4/5] .env exists with API key." -ForegroundColor Green
    }
}

# 5. Schedule weekly research (Task Scheduler) -- Cannoli scans sources
#    Mondays 09:00 even if you don't open tiramisu that week. Findings are
#    proposals only; nothing is applied without `t research apply` + a y/N.
#    Remove with:  Unregister-ScheduledTask -TaskName "Tiramisu Research" -Confirm:$false
$taskName = "Tiramisu Research"
try {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Write-Host "  [5/5] Weekly research already scheduled (Task Scheduler)." -ForegroundColor Green
    } else {
        $action   = New-ScheduledTaskAction -Execute "$INSTALL_DIR\t.bat" -Argument "research all --quiet"
        $trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
        Register-ScheduledTask -TaskName $taskName -Action $action `
            -Trigger $trigger -Settings $settings | Out-Null
        Write-Host "  [5/5] Scheduled weekly research: Mondays 09:00 (Task Scheduler)." -ForegroundColor Green
    }
} catch {
    Write-Host "  [5/5] Could not register scheduled task -- research still" -ForegroundColor Yellow
    Write-Host "        auto-runs in the background when you use tiramisu." -ForegroundColor Yellow
}

# Summary
Write-Host ""
Write-Host "Tiramisu installed." -ForegroundColor Cyan
Write-Host ""
if ($needsRestart) {
    Write-Host "  [!] Open a NEW PowerShell window so PATH refreshes." -ForegroundColor Yellow
}
if ($needsKey) {
    Write-Host "  [!] Edit $envFile and add your ANTHROPIC_API_KEY." -ForegroundColor Yellow
}
if (-not $needsRestart -and -not $needsKey) {
    Write-Host "  Try:  tiramisu" -ForegroundColor Green
}
Write-Host ""
