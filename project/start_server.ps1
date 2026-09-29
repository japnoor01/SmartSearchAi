# SmartSearch AI - FastAPI Server Launcher
Set-Location -Path $PSScriptRoot

$python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Starting SmartSearch AI FastAPI Server on port 8000" -ForegroundColor Green
Write-Host "Endpoint: http://127.0.0.1:8000/docs" -ForegroundColor Yellow
Write-Host "===================================================" -ForegroundColor Cyan

& $python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
