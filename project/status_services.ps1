# =============================================================================
# SmartSearch AI -- Service Status Inspector
# =============================================================================
# Inspects real-time operational status for:
#   1. Background Supervisor (Watchdog PID & Health)
#   2. FastAPI Backend (HTTP 127.0.0.1:8000 /health & /readiness)
#   3. Ngrok Public Tunnel (Process & Active Public HTTPS URL)
#   4. Recent log events from logs/
# =============================================================================

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- SERVICE STATUS INSPECTOR" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$logDir = Join-Path $PSScriptRoot "logs"
$pidFile = Join-Path $logDir "watchdog.pid"

# 1. Watchdog Status
Write-Host "`n[1/3] Background Watchdog Supervisor:" -ForegroundColor Yellow
$watchdogRunning = $false
if (Test-Path $pidFile) {
    try {
        $wPid = [int](Get-Content $pidFile -Raw).Trim()
        $wProc = Get-Process -Id $wPid -ErrorAction SilentlyContinue
        if ($wProc) {
            Write-Host "  STATUS: ACTIVE (PID: $wPid, Memory: $([math]::Round($wProc.WorkingSet64 / 1MB, 1)) MB)" -ForegroundColor Green
            $watchdogRunning = $true
        }
    } catch {}
}
if (-not $watchdogRunning) {
    Write-Host "  STATUS: STOPPED / NOT RUNNING" -ForegroundColor Red
}

# 2. FastAPI Backend Status
Write-Host "`n[2/3] FastAPI Backend (Port 8000):" -ForegroundColor Yellow
$backendHealthy = $false
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 3 -ErrorAction Stop
    $readiness = Invoke-RestMethod -Uri "http://127.0.0.1:8000/readiness" -Method Get -TimeoutSec 3 -ErrorAction Stop
    
    Write-Host "  STATUS: ONLINE & READY" -ForegroundColor Green
    Write-Host "  Model Loaded:   $($health.model_loaded)" -ForegroundColor Gray
    Write-Host "  Index Loaded:   $($health.index_connected)" -ForegroundColor Gray
    Write-Host "  Compute Device: $($health.device.ToUpper())" -ForegroundColor Gray
    Write-Host "  Uptime:         $([math]::Round($readiness.uptime_seconds, 1)) seconds" -ForegroundColor Gray
    $backendHealthy = $true
} catch {
    Write-Host "  STATUS: OFFLINE / UNRESPONSIVE ($($_.Exception.Message))" -ForegroundColor Red
}

# 3. Ngrok Public Tunnel Status
Write-Host "`n[3/3] Ngrok Tunnel:" -ForegroundColor Yellow
$ngrokProc = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ngrokProc) {
    Write-Host "  Process: RUNNING (PID: $($ngrokProc.Id))" -ForegroundColor Green
    # Query ngrok local API
    try {
        $tunnels = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -Method Get -TimeoutSec 2 -ErrorAction Stop
        if ($tunnels.tunnels -and $tunnels.tunnels.Count -gt 0) {
            foreach ($t in $tunnels.tunnels) {
                Write-Host "  Public URL:   $($t.public_url) -> $($t.config.addr)" -ForegroundColor Cyan
            }
        } else {
            Write-Host "  Public URL:   Establishing tunnel..." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Public URL:   Active (API inspection at :4040 initializing)" -ForegroundColor Gray
    }
} else {
    Write-Host "  STATUS: STOPPED / NOT RUNNING" -ForegroundColor Red
}

# 4. Recent Logs Preview
Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
Write-Host "Recent Log Entries:" -ForegroundColor Yellow

$supervisorLog = Join-Path $logDir "supervisor.log"
if (Test-Path $supervisorLog) {
    Write-Host "`n-- [logs/supervisor.log (last 3 lines)] --" -ForegroundColor Gray
    Get-Content $supervisorLog -Tail 3 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

$fastapiLog = Join-Path $logDir "fastapi.log"
if (Test-Path $fastapiLog) {
    Write-Host "`n-- [logs/fastapi.log (last 3 lines)] --" -ForegroundColor Gray
    Get-Content $fastapiLog -Tail 3 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

$ngrokLog = Join-Path $logDir "ngrok.log"
if (Test-Path $ngrokLog) {
    Write-Host "`n-- [logs/ngrok.log (last 3 lines)] --" -ForegroundColor Gray
    Get-Content $ngrokLog -Tail 3 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
