@echo off
REM ---------------------------------------------------------------------------
REM Double-click launcher for BotTraderX5.
REM Boots the local bot if it is not already running, then opens the dashboard.
REM Safe to double-click twice - it will not start a second instance.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0boot_bot.ps1"
