@echo off
REM =============================================================================
REM SmartSearch AI -- Remove Startup Shortcut (Batch Launcher)
REM =============================================================================
title SmartSearch AI -- Remove Startup Shortcut
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0remove_startup_shortcut.ps1"
echo.
pause
