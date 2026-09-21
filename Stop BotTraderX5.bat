@echo off
REM ---------------------------------------------------------------------------
REM Double-click to stop BotTraderX5 (stops the server process and frees port 5000).
REM NOTE: open positions stay open at Deriv, but their stop-loss/take-profit are
REM only enforced while the bot runs - so they are unprotected until you boot again.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_bot.ps1"
