@echo off
REM ---------------------------------------------------------------------------
REM Installs the BotTraderX5 watchdog WITHOUT admin rights:
REM   * starts the bot automatically at every logon
REM   * relaunches it within ~45s if the process ever dies
REM Safe to run more than once.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_watchdog.ps1"
