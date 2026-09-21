# ---------------------------------------------------------------------------
# BotTraderX5 launcher — boots the local server and opens the dashboard.
#
# Safe to run twice: a SECOND instance would mean two AutoTraders trading the
# same account, so if one is already running this only opens the browser.
#
# NOTE ON THE PROXY: this machine has a corporate proxy configured
# (10.10.4.5:80) which also intercepts requests to 127.0.0.1, making a local
# health check fail with "502 Operation not permitted". The script therefore
# clears the default proxy before probing localhost. That is a LOCAL setting in
# this process only — nothing else is affected.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$url = 'http://127.0.0.1:5000'
$venv = Join-Path $root '.venv\Scripts\python.exe'

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*app.py*' }
}

# --- already running? ------------------------------------------------------
$running = @(Get-BotProcesses)
if ($running.Count -gt 0) {
    Write-Host "BotTraderX5 is ALREADY RUNNING (pid $($running[0].ProcessId))." -ForegroundColor Yellow
    Write-Host "Not starting a second instance - that would double-trade the account." -ForegroundColor Yellow
} else {
    if (-not (Test-Path $venv)) {
        Write-Host "Python venv not found at:" -ForegroundColor Red
        Write-Host "  $venv" -ForegroundColor Red
        Read-Host 'Press Enter to close'
        exit 1
    }
    Write-Host "Starting BotTraderX5..." -ForegroundColor Cyan
    Start-Process -FilePath $venv -ArgumentList 'app.py' -WorkingDirectory $root -WindowStyle Minimized
}

# --- wait until it answers -------------------------------------------------
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy   # bypass proxy for localhost
$up = $false
for ($i = 1; $i -le 45; $i++) {
    try {
        Invoke-RestMethod -Uri "$url/api/auto/status" -TimeoutSec 3 | Out-Null
        $up = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if ($up) {
    Write-Host "Bot is UP and answering on $url" -ForegroundColor Green
} else {
    Write-Host "Started, but $url did not answer within 45s." -ForegroundColor Red
    Write-Host "Check the minimised window / server log for errors." -ForegroundColor Red
}

Start-Process $url      # open the dashboard
Start-Sleep -Seconds 3
