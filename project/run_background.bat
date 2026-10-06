@echo off
REM =============================================================================
REM SmartSearch AI -- Background Runner (Detached Launcher)
REM =============================================================================
title SmartSearch AI Background Runner
cd /d "%~dp0"
echo Starting SmartSearch AI background services (FastAPI + Ngrok)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""%~dp0run_background.ps1""'; CurrentDirectory = '%~dp0'} | Out-Null"
echo.
echo =============================================================================
echo SmartSearch AI services successfully launched in the background!
echo - FastAPI backend binding:  http://127.0.0.1:8000
set "DOMAIN=emphatic-tackle-monetary.ngrok-free.dev"
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,2 delims==" %%A in ("%~dp0.env") do (
        if "%%A"=="NGROK_DOMAIN" set "DOMAIN=%%B"
    )
)
echo - Ngrok public tunnel:      https://%DOMAIN%
echo.
echo You may now safely close this window, VS Code, or any other IDE.
echo - To check status: double-click status_services.bat
echo - To stop:         double-click stop_services.bat
echo =============================================================================
ping 127.0.0.1 -n 4 >nul
