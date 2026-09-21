# ---------------------------------------------------------------------------
# BotTraderX5 stopper — stops the local server (parent + child process).
#
# A normal run shows TWO python rows: the venv `python.exe` LAUNCHER (parent)
# plus the child that actually holds port 5000. Killing the parent with /T takes
# the child with it, so both go. Afterwards the port is verified free.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*app.py*' }
}

$procs = @(Get-BotProcesses)
if ($procs.Count -eq 0) {
    Write-Host "BotTraderX5 is not running - nothing to stop." -ForegroundColor Yellow
} else {
    foreach ($p in $procs) {
        Write-Host "Stopping pid $($p.ProcessId) ..." -ForegroundColor Cyan
        taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Null
    }
}

Start-Sleep -Seconds 2
$listening = @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)
if ($listening.Count -eq 0) {
    Write-Host "Port 5000 is free. Bot stopped." -ForegroundColor Green
} else {
    Write-Host "Something is STILL listening on port 5000 (pid $($listening[0].OwningProcess))." -ForegroundColor Red
    Write-Host "Open positions remain safe at Deriv - they keep running without the bot," -ForegroundColor Yellow
    Write-Host "but their stop-loss/take-profit will NOT be enforced while it is off." -ForegroundColor Yellow
}

Start-Sleep -Seconds 2
