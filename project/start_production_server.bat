@echo off
title SmartSearch AI - Production Server
cd /d "%~dp0"
echo =============================================================
echo Starting SmartSearch AI in PRODUCTION mode on port 8000
echo (Auto-reload: OFF, Strict CORS: ACTIVE, Rate Limit: ACTIVE)
echo =============================================================
echo.
set SMARTSEARCH_ENV=production
..\.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --workers 1
pause
