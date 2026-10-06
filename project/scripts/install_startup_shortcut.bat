@echo off
REM =============================================================================
REM SmartSearch AI -- Install Startup Shortcut (Batch Launcher)
REM =============================================================================
title SmartSearch AI -- Install Startup Shortcut
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_startup_shortcut.ps1"
echo.
pause
