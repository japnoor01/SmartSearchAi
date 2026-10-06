@echo off
REM =============================================================================
REM SmartSearch AI -- Stop Background Services (Batch Launcher)
REM =============================================================================
title SmartSearch AI -- Stop Background Services
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_services.ps1"
echo.
pause
