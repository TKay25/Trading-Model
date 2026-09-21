@echo off
REM ---------------------------------------------------------------------------
REM RUN THIS ONCE. Registers the BotTraderX5 watchdog with Windows so the bot
REM boots at log on and restarts itself if it ever dies.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_watchdog.ps1"
