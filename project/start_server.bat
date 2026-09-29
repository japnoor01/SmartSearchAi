@echo off
title SmartSearch AI - FastAPI Server
cd /d "%~dp0"
echo ===================================================
echo Starting SmartSearch AI FastAPI Server on port 8000
echo ===================================================
echo.
..\.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
pause
