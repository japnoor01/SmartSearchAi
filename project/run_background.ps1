# =============================================================================
# SmartSearch AI -- Production Background Supervisor & Watchdog
# =============================================================================
# Autonomously manages the lifecycle of:
#   1. FastAPI Backend (Uvicorn 127.0.0.1:8000, 1 worker, production mode)
#   2. Ngrok Public Tunnel (https://YOUR_DOMAIN -> 127.0.0.1:8000)
#
# Key Features:
#   - Runs completely in the background without requiring VS Code or open terminals.
#   - Singleton guard: prevents duplicate supervisors or zombie duplicate processes.
#   - Strict startup ordering: FastAPI starts first, verifies /health before ngrok starts.
#   - Watchdog auto-restart: automatically revives FastAPI or ngrok if either crashes.
#   - Structured file logging with automatic size-based log rotation (max 5 MB).
# =============================================================================

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$supervisorLog = Join-Path $logDir "supervisor.log"
$fastapiLog    = Join-Path $logDir "fastapi.log"
$fastapiErrLog = Join-Path $logDir "fastapi_err.log"
$ngrokLog      = Join-Path $logDir "ngrok.log"
$pidFile       = Join-Path $logDir "watchdog.pid"

function Write-SupervisorLog {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$timestamp] [$Level] $Message"
    Add-Content -Path $supervisorLog -Value $line
}

function Rotate-LogFile {
    param([string]$FilePath, [int]$MaxBytes = 5242880) # 5 MB default
    if (Test-Path $FilePath) {
        $item = Get-Item $FilePath
        if ($item.Length -gt $MaxBytes) {
            $oldPath = "$FilePath.old"
            if (Test-Path $oldPath) { Remove-Item $oldPath -Force }
            Move-Item -Path $FilePath -Destination $oldPath -Force
            Write-SupervisorLog "Rotated log file: $FilePath -> $oldPath" "INFO"
        }
    }
}

# -----------------------------------------------------------------------------
# 1. Singleton Guard (Prevent Multiple Background Watchdogs)
# -----------------------------------------------------------------------------
if (Test-Path $pidFile) {
    $existingPid = $null
    try {
        $existingPid = [int](Get-Content $pidFile -Raw).Trim()
    } catch {}

    if ($existingPid) {
        $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($existingProc) {
            $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $existingPid" -ErrorAction SilentlyContinue).CommandLine
            if ($cmd -and $cmd -match "run_background\.ps1") {
                Write-SupervisorLog "Another watchdog supervisor is already running (PID $existingPid). Exiting duplicate." "WARN"
                exit 0
            }
        }
    }
}

# Register current process PID
$PID | Out-File -FilePath $pidFile -Encoding ascii -Force
Write-SupervisorLog "=== SmartSearch AI Background Supervisor started (PID $PID) ===" "INFO"

# -----------------------------------------------------------------------------
# 2. Locate Executables & Parse Configuration
# -----------------------------------------------------------------------------
$python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python.exe"
}

$ngrok = Join-Path $PSScriptRoot "ngrok.exe"
if (-not (Test-Path $ngrok)) {
    $ngrokCmd = Get-Command ngrok.exe -ErrorAction SilentlyContinue
    if ($ngrokCmd) { $ngrok = $ngrokCmd.Source } else { $ngrok = "ngrok" }
}

# Parse .env
$envFile = Join-Path $PSScriptRoot ".env"
$authToken = $null
$domain = $null
$port = 8000
$hostAddress = "127.0.0.1"

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^NGROK_AUTHTOKEN\s*=\s*(.+)$') { $authToken = $Matches[1].Trim() }
        if ($line -match '^NGROK_DOMAIN\s*=\s*(.+)$') { $domain = $Matches[1].Trim() }
        if ($line -match '^SMARTSEARCH_PORT\s*=\s*(\d+)$') { $port = [int]$Matches[1].Trim() }
        if ($line -match '^SMARTSEARCH_HOST\s*=\s*(.+)$') { $hostAddress = $Matches[1].Trim() }
    }
}

# Ensure ngrok auth token is configured if present
if ($authToken) {
    try {
        & $ngrok config add-authtoken $authToken | Out-Null
    } catch {
        Write-SupervisorLog "Warning configuring ngrok auth token: $_" "WARN"
    }
}

# -----------------------------------------------------------------------------
# 3. Helper Functions for Process Management & Health Verification
# -----------------------------------------------------------------------------

function Test-FastAPIHealth {
    param([string]$HealthUrl = "http://127.0.0.1:$port/health", [int]$TimeoutSec = 3)
    try {
        $resp = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($resp.status -eq "ok" -and $resp.model_loaded -eq $true -and $resp.index_connected -eq $true)
    } catch {
        return $false
    }
}

function Stop-PortOccupant {
    param([int]$TargetPort = 8000)
    try {
        $connections = Get-NetTCPConnection -LocalPort $TargetPort -ErrorAction SilentlyContinue
        foreach ($conn in $connections) {
            $owningProcId = $conn.OwningProcess
            if ($owningProcId -and $owningProcId -gt 4) {
                Write-SupervisorLog "Stopping stale process occupying port $TargetPort (PID $owningProcId)..." "WARN"
                Stop-Process -Id $owningProcId -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-SupervisorLog "Error clearing port ${TargetPort}: $_" "WARN"
    }
}

function Stop-DuplicateNgrok {
    param([int]$KeepPid = 0)
    try {
        $ngrokProcs = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
        if ($ngrokProcs) {
            foreach ($p in $ngrokProcs) {
                if ($KeepPid -eq 0 -or $p.Id -ne $KeepPid) {
                    Write-SupervisorLog "Stopping duplicate ngrok instance (PID $($p.Id))..." "WARN"
                    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                }
            }
        }
    } catch {
        Write-SupervisorLog "Error stopping duplicate ngrok: $_" "WARN"
    }
}

function Start-FastAPIService {
    Rotate-LogFile $fastapiLog
    Rotate-LogFile $fastapiErrLog
    Write-SupervisorLog "Launching FastAPI production server (uvicorn 127.0.0.1:$port, workers=1)..." "INFO"

    $env:SMARTSEARCH_ENV = "production"
    $env:SMARTSEARCH_HOST = $hostAddress
    $env:SMARTSEARCH_PORT = [string]$port
    $env:SMARTSEARCH_DEVICE = "cpu"

    $args = "-m uvicorn src.api.app:app --host $hostAddress --port $port --workers 1"
    
    $proc = Start-Process -FilePath $python `
                          -ArgumentList $args `
                          -WorkingDirectory $PSScriptRoot `
                          -RedirectStandardOutput $fastapiLog `
                          -RedirectStandardError $fastapiErrLog `
                          -WindowStyle Hidden `
                          -PassThru
    return $proc
}

function Start-NgrokService {
    Rotate-LogFile $ngrokLog
    Write-SupervisorLog "Launching ngrok tunnel to port $port (domain: $domain)..." "INFO"

    $args = if ($domain) {
        "http --domain=$domain $port --log=`"$ngrokLog`" --log-format=logfmt --log-level=info"
    } else {
        "http $port --log=`"$ngrokLog`" --log-format=logfmt --log-level=info"
    }

    $proc = Start-Process -FilePath $ngrok `
                          -ArgumentList $args `
                          -WorkingDirectory $PSScriptRoot `
                          -WindowStyle Hidden `
                          -PassThru
    return $proc
}

# -----------------------------------------------------------------------------
# 4. Initial Service Launch Sequence
# -----------------------------------------------------------------------------

# Step A: Check if FastAPI is already healthy on port 8000
$fastapiHealthy = Test-FastAPIHealth
$fastapiProc = $null
$ready = $false

if ($fastapiHealthy) {
    Write-SupervisorLog "FastAPI is already running and responsive on http://127.0.0.1:$port/health." "INFO"
    $ready = $true
} else {
    Stop-PortOccupant -TargetPort $port
    Start-Sleep -Seconds 1
    $fastapiProc = Start-FastAPIService

    # Wait up to 30 seconds for FastAPI /health to respond 200 OK with model and index connected
    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-FastAPIHealth) {
            $ready = $true
            Write-SupervisorLog "FastAPI readiness confirmed after $i seconds." "INFO"
            break
        }
    }

    if (-not $ready) {
        Write-SupervisorLog "FastAPI failed health check after 30 seconds. Check logs/fastapi.log." "ERROR"
    }
}

# Step B: Launch ngrok tunnel ONLY after FastAPI is confirmed alive and healthy
$ngrokProc = $null

if ($ready -or (Test-FastAPIHealth)) {
    Stop-DuplicateNgrok
    $existingNgrok = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($existingNgrok) {
        Write-SupervisorLog "Ngrok process is already running (PID $($existingNgrok.Id))." "INFO"
        $ngrokProc = $existingNgrok
        Stop-DuplicateNgrok -KeepPid $existingNgrok.Id
    } else {
        $ngrokProc = Start-NgrokService
        Start-Sleep -Seconds 3
        Write-SupervisorLog "Ngrok tunnel initiated. Verify in logs/ngrok.log or test your public domain." "INFO"
    }
} else {
    Write-SupervisorLog "Holding back ngrok launch because FastAPI backend is not yet healthy." "WARN"
}

# -----------------------------------------------------------------------------
# 5. Supervision & Auto-Restart Watchdog Loop
# -----------------------------------------------------------------------------
Write-SupervisorLog "Entering active watchdog loop (health check interval: 10s)..." "INFO"

$consecutiveFastAPIFailures = 0

try {
    while ($true) {
        Start-Sleep -Seconds 10

        # Periodic log rotation check
        Rotate-LogFile $supervisorLog
        Rotate-LogFile $fastapiLog
        Rotate-LogFile $fastapiErrLog
        Rotate-LogFile $ngrokLog

        # Check 1: FastAPI Health
        $isHealthy = Test-FastAPIHealth
        if ($isHealthy) {
            $consecutiveFastAPIFailures = 0
        } else {
            $consecutiveFastAPIFailures++
            Write-SupervisorLog "FastAPI health check probe failed (failure count: $consecutiveFastAPIFailures)." "WARN"

            if ($consecutiveFastAPIFailures -ge 2) {
                Write-SupervisorLog "FastAPI unresponsive or crashed. Initiating automatic restart..." "ERROR"
                Stop-PortOccupant -TargetPort $port
                Start-Sleep -Seconds 1
                $fastapiProc = Start-FastAPIService

                # Wait for revived instance to be ready
                for ($w = 1; $w -le 20; $w++) {
                    Start-Sleep -Seconds 1
                    if (Test-FastAPIHealth) {
                        Write-SupervisorLog "FastAPI successfully revived and verified healthy." "INFO"
                        $consecutiveFastAPIFailures = 0
                        break
                    }
                }
            }
        }

        # Check 2: Ngrok Process & Health
        # Only ensure ngrok is active if FastAPI is currently healthy!
        if (Test-FastAPIHealth) {
            $ngrokAlive = $false
            if ($ngrokProc -and -not $ngrokProc.HasExited) {
                $ngrokAlive = $true
            } else {
                # Fallback process search
                $ngrokCheck = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
                if ($ngrokCheck) {
                    $ngrokAlive = $true
                    $ngrokProc = $ngrokCheck | Select-Object -First 1
                    Stop-DuplicateNgrok -KeepPid $ngrokProc.Id
                }
            }

            if (-not $ngrokAlive) {
                Write-SupervisorLog "Ngrok process died or was not started. Initiating automatic restart..." "WARN"
                Stop-DuplicateNgrok
                $ngrokProc = Start-NgrokService
                Write-SupervisorLog "Ngrok restart initiated." "INFO"
            }
        }
    }
} finally {
    Write-SupervisorLog "Supervisor shutting down. Cleaning up PID file..." "INFO"
    if (Test-Path $pidFile) {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}
