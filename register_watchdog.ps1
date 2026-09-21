# ---------------------------------------------------------------------------
# Registers the BotTraderX5 watchdog as a Windows Scheduled Task.
#
#   RUN THIS ONCE.  Double-click "Register Watchdog.bat", or:
#       powershell -NoProfile -ExecutionPolicy Bypass -File .\register_watchdog.ps1
#
# What it creates — one task named "BotTraderX5 Watchdog":
#   * AT LOG ON          -> boots the bot automatically after you sign in
#   * EVERY 3 MINUTES    -> restarts it if the process has died
#
# No password is stored: the task runs "only when the user is logged on", which
# is exactly what a desktop trading bot needs. There is no always-running
# watchdog process — Task Scheduler itself is the loop, so there is nothing to
# supervise the supervisor.
#
# To remove it:  Unregister-ScheduledTask -TaskName 'BotTraderX5 Watchdog'
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'

$root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$script   = Join-Path $root 'watchdog_bot.ps1'
$taskName = 'BotTraderX5 Watchdog'

if (-not (Test-Path $script)) {
    Write-Host "watchdog_bot.ps1 not found next to this script: $script" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

Write-Host "Registering scheduled task '$taskName' ..." -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $script) `
    -WorkingDirectory $root

# Trigger 1: at log on lets the bot come up by itself after a reboot/sign-in.
$tLogon = New-ScheduledTaskTrigger -AtLogOn

# Trigger 2: the watchdog loop. NOTE the quirk — the repetition duration does
# NOT accept [TimeSpan]::MaxValue on Windows PowerShell 5.1 ("parameter is
# incorrect"); a long finite duration behaves as effectively indefinite.
$tRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 3) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

try {
    # -ErrorAction Stop matters: without it a permission failure ("Access is
    # denied", HRESULT 0x80070005) is only a NON-terminating error, the catch
    # never fires, and the script prints a false success message.
    Register-ScheduledTask -TaskName $taskName -Action $action `
        -Trigger $tLogon, $tRepeat -Settings $settings -Force -ErrorAction Stop `
        -Description ('Keeps BotTraderX5 alive: boots it at log on and restarts it every 3 minutes if the process has died. Runs watchdog_bot.ps1, which is a no-op when the bot is healthy.') | Out-Null
} catch {
    Write-Host ""
    Write-Host "FAILED to register the task: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Creating a scheduled task requires ADMINISTRATOR rights." -ForegroundColor Yellow
    Write-Host "Fix: right-click 'Register Watchdog.bat' -> 'Run as administrator'." -ForegroundColor Yellow
    Read-Host 'Press Enter to close'
    exit 1
}

# Believe the registry, not the absence of an exception.
$info = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $info) {
    Write-Host "Registration reported no error, but the task does not exist." -ForegroundColor Red
    Write-Host "Try again from an elevated PowerShell." -ForegroundColor Yellow
    Read-Host 'Press Enter to close'
    exit 1
}

Write-Host "Registered. Verifying..." -ForegroundColor Cyan
$state = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host ("  state      : {0}" -f $info.State)
Write-Host ("  next run   : {0}" -f $state.NextRunTime)
Write-Host ("  last result: {0}" -f $state.LastTaskResult)
Write-Host ""
Write-Host "DONE. The bot will now start at log on and self-heal within ~3 minutes." -ForegroundColor Green
Write-Host "Log of watchdog activity: $(Join-Path $root 'watchdog.log')" -ForegroundColor Green
Read-Host 'Press Enter to close'
