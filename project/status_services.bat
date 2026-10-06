@echo off
REM =============================================================================
REM SmartSearch AI -- Service Status Inspector (Batch Launcher)
REM =============================================================================
title SmartSearch AI -- Service Status Inspector
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0status_services.ps1"
echo.
pause
