@echo off
REM =============================================================================
REM SmartSearch AI -- Endpoint Verification Suite (Batch Launcher)
REM =============================================================================
title SmartSearch AI -- Endpoint Verification Suite
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0test_endpoints.ps1"
echo.
pause
