# SmartSearch AI - Production Server Launcher
Set-Location -Path $PSScriptRoot

$python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "Starting SmartSearch AI in PRODUCTION mode on port 8000" -ForegroundColor Green
Write-Host "Auto-reload: OFF | Strict CORS: ACTIVE | Rate Limit: ACTIVE" -ForegroundColor Yellow
Write-Host "=============================================================" -ForegroundColor Cyan

$env:SMARTSEARCH_ENV = "production"
& $python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --workers 1
