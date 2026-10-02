@echo off
REM =============================================================================
REM SmartSearch AI -- Permanent Ngrok Tunnel One-Click Launcher (Batch)
REM =============================================================================
title SmartSearch AI -- Permanent Ngrok Tunnel Launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_ngrok.ps1"
pause
