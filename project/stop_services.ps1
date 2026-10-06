# =============================================================================
# SmartSearch AI -- Stop Background Services
# =============================================================================
# Cleanly terminates:
#   1. Background Supervisor & Watchdog (run_background.ps1)
#   2. Ngrok Tunnel (ngrok.exe)
#   3. FastAPI Backend (python.exe / uvicorn on port 8000)
# =============================================================================

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- STOPPING BACKGROUND SERVICES" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$logDir = Join-Path $PSScriptRoot "logs"
$pidFile = Join-Path $logDir "watchdog.pid"

# 1. Stop Watchdog Supervisor
Write-Host "`n[1/3] Stopping background supervisor..." -ForegroundColor Yellow
if (Test-Path $pidFile) {
    try {
        $watchdogPid = [int](Get-Content $pidFile -Raw).Trim()
        if ($watchdogPid) {
            $proc = Get-Process -Id $watchdogPid -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $watchdogPid -Force -ErrorAction SilentlyContinue
                Write-Host "Supervisor process (PID $watchdogPid) stopped." -ForegroundColor Green
            }
        }
    } catch {}
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
} else {
    # Fallback search for any powershell process running run_background
    Get-CimInstance Win32_Process -Filter "Name like 'powershell%.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.CommandLine -and $_.CommandLine -match "run_background\.ps1") {
            Write-Host "Stopping supervisor (PID $($_.ProcessId))..." -ForegroundColor Yellow
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}
Write-Host "Supervisor stopped." -ForegroundColor Green

# 2. Stop Ngrok Tunnels
Write-Host "`n[2/3] Stopping ngrok tunnels..." -ForegroundColor Yellow
$ngrokProcs = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
if ($ngrokProcs) {
    foreach ($p in $ngrokProcs) {
        Write-Host "Stopping ngrok (PID $($p.Id))..." -ForegroundColor Yellow
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "All ngrok instances terminated." -ForegroundColor Green
} else {
    Write-Host "No active ngrok process found." -ForegroundColor Gray
}

# 3. Stop FastAPI on Port 8000
Write-Host "`n[3/3] Stopping FastAPI backend (Port 8000)..." -ForegroundColor Yellow
try {
    $connections = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        $targetPid = $conn.OwningProcess
        if ($targetPid -and $targetPid -gt 4) {
            $pName = (Get-Process -Id $targetPid -ErrorAction SilentlyContinue).ProcessName
            Write-Host "Terminating port 8000 listener (PID $targetPid, $pName)..." -ForegroundColor Yellow
            Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
        }
    }
} catch {}

Start-Sleep -Seconds 1
$checkPort = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $checkPort) {
    Write-Host "Port 8000 successfully freed. Backend stopped." -ForegroundColor Green
} else {
    Write-Host "Warning: Port 8000 still has active listeners." -ForegroundColor Red
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "  All SmartSearch AI background services have stopped." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
