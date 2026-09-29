@echo off
REM =============================================================================
REM SmartSearch AI -- Cloudflare Tunnel One-Click Launcher (Batch)
REM =============================================================================
title SmartSearch AI -- Cloudflare Tunnel Launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tunnel.ps1"
pause
