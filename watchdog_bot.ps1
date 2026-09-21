# ---------------------------------------------------------------------------
# BotTraderX5 watchdog — keeps the bot alive.
#
# Designed to be run by Windows Task Scheduler on a repeating trigger, so there
# is NO always-running watchdog process to babysit (and nothing to supervise the
# supervisor). Each run:
#   * bot already running -> exit silently (no log noise)
#   * bot NOT running     -> relaunch it headless, with stdout/stderr to files
#
# SAFE TO RESTART NOW: a relaunch restores every open position from
# trade_paths.json with its ORIGINAL stop/target, and adoption refuses to install
# an already-breached stop — so an automatic restart can no longer liquidate the
# book (that was the pre-2026-09-21 behaviour).
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'

# $PSScriptRoot is correct even when this script is invoked with `&` from
# watchdog_loop.ps1; fall back for older/odd hosts.
$root = if ($PSScriptRoot) { $PSScriptRoot }
        else { Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $root

$log  = Join-Path $root 'watchdog.log'
$venv = Join-Path $root '.venv\Scripts\python.exe'
$out  = Join-Path $root 'bot_run.log'
$err  = Join-Path $root 'bot_err.log'

function Write-Log($msg) {
    Add-Content -Path $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    # keep the log from growing forever: retain the last 400 lines
    $lines = @(Get-Content $log -ErrorAction SilentlyContinue)
    if ($lines.Count -gt 400) {
        $lines[-400..-1] | Set-Content $log
    }
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*app.py*' }
}

# --- healthy? do nothing ---------------------------------------------------
$running = @(Get-BotProcesses)
if ($running.Count -gt 0) { exit 0 }

# --- something is listening on 5000 but no app.py? -------------------------
# Could be a stale/foreign listener; do NOT start a second bot blindly.
$listening = @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)
if ($listening.Count -gt 0) {
    Write-Log "port 5000 held by pid $($listening[0].OwningProcess) but no app.py process - NOT relaunching"
    exit 1
}

if (-not (Test-Path $venv)) {
    Write-Log "ERROR python venv missing: $venv"
    exit 1
}

Write-Log "bot NOT running -> relaunching"
Start-Process -FilePath $venv -ArgumentList 'app.py' -WorkingDirectory $root `
              -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err

# give it time to bind + restore positions before reporting
Start-Sleep -Seconds 25

$after = @(Get-BotProcesses)
if ($after.Count -gt 0) {
    Write-Log "relaunched OK (pid $($after[0].ProcessId))"
    exit 0
}
Write-Log "FAILED to relaunch - see bot_err.log"
exit 1
