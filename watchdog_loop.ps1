# ---------------------------------------------------------------------------
# BotTraderX5 watchdog LOOP — keeps the bot alive with NO admin rights.
#
# Windows Task Scheduler needs Administrator, so this takes the other route:
# a single long-lived loop that (a) starts the bot when you log in and
# (b) relaunches it within ~45s if the process ever dies.
#
# The actual checks live in watchdog_bot.ps1, which exits silently when the bot
# is healthy — so a healthy run costs one process query and nothing else.
#
# A named mutex guarantees only ONE loop exists. Two loops would race to relaunch
# the bot, and a race could produce two bots (or one that fails to bind port 5000).
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'
$root   = $PSScriptRoot
$worker = Join-Path $root 'watchdog_bot.ps1'

if (-not (Test-Path $worker)) { exit 1 }

$mutex = New-Object System.Threading.Mutex($false, 'BotTraderX5WatchdogLoop')
$mine = $false
try {
    $mine = $mutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $mine = $true          # previous loop was killed; we inherit the lock
}
if (-not $mine) { exit 0 }  # another loop is already running

while ($true) {
    try {
        & $worker          # no-op when healthy; relaunches + logs when not
    } catch {
        # never let one bad iteration kill the watchdog
    }
    Start-Sleep -Seconds 45
}
