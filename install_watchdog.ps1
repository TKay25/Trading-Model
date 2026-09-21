# ---------------------------------------------------------------------------
# Installs the BotTraderX5 watchdog WITHOUT administrator rights.
#
# What it does:
#   1. drops a launcher in your personal Startup folder, so the watchdog (and
#      therefore the bot) starts every time you log in
#   2. starts the watchdog right now, so it is protected immediately
#   3. verifies both
#
# This is the no-admin alternative to register_watchdog.ps1 (Task Scheduler needs
# Administrator; the Startup folder is per-user and does not).
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$loop = Join-Path $root 'watchdog_loop.ps1'
if (-not (Test-Path $loop)) {
    Write-Host "watchdog_loop.ps1 not found next to this script." -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

$startup = [Environment]::GetFolderPath('Startup')
$cmdPath = Join-Path $startup 'BotTraderX5 Watchdog.cmd'

Write-Host "Startup folder: $startup" -ForegroundColor Cyan
Write-Host "Creating launcher: $cmdPath" -ForegroundColor Cyan

# A .cmd launcher is the most reliable thing to put in Startup: it is not subject
# to shortcut-target breakage and it can pass the execution-policy bypass that a
# double-clicked .ps1 would need.
$lines = @(
    '@echo off',
    ('REM Runs the BotTraderX5 watchdog loop hidden at every logon.'),
    ('powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $loop)
)
Set-Content -Path $cmdPath -Value $lines -Encoding ASCII

if (-not (Test-Path $cmdPath)) {
    Write-Host "Could not write the launcher." -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}
Write-Host "  launcher created" -ForegroundColor Green

# Already running? Don't start a second loop (the mutex would drop it anyway).
function Get-LoopProcesses {
    Get-CimInstance Win32_Process -Filter "Name like '%powershell%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*watchdog_loop.ps1*' }
}

$existing = @(Get-LoopProcesses)
if ($existing.Count -gt 0) {
    Write-Host "  watchdog loop already running (pid $($existing[0].ProcessId))" -ForegroundColor Green
} else {
    Write-Host "Starting the watchdog loop now..." -ForegroundColor Cyan
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $loop) `
        -WindowStyle Hidden
    Start-Sleep -Seconds 6
    $now = @(Get-LoopProcesses)
    if ($now.Count -gt 0) {
        Write-Host "  watchdog loop running (pid $($now[0].ProcessId))" -ForegroundColor Green
    } else {
        Write-Host "  watchdog loop did NOT start - check the script manually" -ForegroundColor Red
    }
}

$bot = @(Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like '*app.py*' })
Write-Host ""
Write-Host ("Bot process(es): {0}" -f $bot.Count) -ForegroundColor Cyan
Write-Host ""
Write-Host "DONE (no admin needed)." -ForegroundColor Green
Write-Host "  * the bot now starts automatically when you log in" -ForegroundColor Green
Write-Host "  * if it ever dies, it comes back within ~45 seconds" -ForegroundColor Green
Write-Host ("  * watchdog activity log: {0}" -f (Join-Path $root 'watchdog.log')) -ForegroundColor Green
Write-Host ""
Write-Host "To remove: delete 'BotTraderX5 Watchdog.cmd' from the Startup folder." -ForegroundColor Yellow
Read-Host 'Press Enter to close'
