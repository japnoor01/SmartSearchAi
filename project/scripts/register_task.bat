@echo off
REM =============================================================================
REM SmartSearch AI -- Register Task Scheduler (UAC Auto-Elevating)
REM =============================================================================
title SmartSearch AI -- Register Windows Startup Task
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator privileges to register Task Scheduler task...
    powershell -Command "Start-Process cmd -ArgumentList '/c `"%~f0`"' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_task.ps1"
echo.
pause
