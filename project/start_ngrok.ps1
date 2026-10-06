# =============================================================================
# SmartSearch AI -- Permanent Ngrok Tunnel Launcher (Interactive Mode)
# =============================================================================
# Connects local FastAPI backend (Port 8000) to your permanent Ngrok domain.
#
# Startup Sequence:
#   1. Checks if FastAPI backend is responding at http://127.0.0.1:8000/health
#   2. Loads NGROK_AUTHTOKEN and NGROK_DOMAIN from .env
#   3. Connects ngrok to local port 8000
# =============================================================================

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- PERMANENT NGROK LAUNCHER" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Verify FastAPI backend health
Write-Host "`n[1/3] Verifying FastAPI backend on port 8000..." -ForegroundColor Yellow
$fastapiReady = $false
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 3 -ErrorAction Stop
    if ($resp.status -eq "ok" -or $resp.model_loaded -eq $true) {
        $fastapiReady = $true
        Write-Host "FastAPI backend is ONLINE and healthy (Port 8000)." -ForegroundColor Green
    }
} catch {
    Write-Host "FastAPI is not currently responding on http://127.0.0.1:8000/health." -ForegroundColor Red
    Write-Host "Please start the backend first via:" -ForegroundColor Yellow
    Write-Host "  - run_background.vbs (background mode)" -ForegroundColor Gray
    Write-Host "  - start_production_server.bat (interactive console mode)`n" -ForegroundColor Gray
    
    $choice = Read-Host "Would you like to launch the production server now? (Y/N)"
    if ($choice -match '^[Yy]') {
        Write-Host "Launching production server..." -ForegroundColor Gray
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c start start_production_server.bat" -WorkingDirectory $PSScriptRoot
        Write-Host "Waiting 6 seconds for model and index to load..." -ForegroundColor Gray
        Start-Sleep -Seconds 6
    } else {
        Write-Host "Exiting ngrok launcher." -ForegroundColor Yellow
        exit 1
    }
}

# 2. Check for .env credentials
$envFile = Join-Path $PSScriptRoot ".env"
$authToken = $null
$domain = $null

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^NGROK_AUTHTOKEN\s*=\s*(.+)$') { $authToken = $Matches[1].Trim() }
        if ($line -match '^NGROK_DOMAIN\s*=\s*(.+)$') { $domain = $Matches[1].Trim() }
    }
}

if (-not $authToken) {
    Write-Host "`n[!] Ngrok Auth Token is required (Free at https://dashboard.ngrok.com/get-started/your-authtoken)" -ForegroundColor Yellow
    $authToken = Read-Host "Paste your Ngrok Authtoken"
    if ($authToken) {
        Add-Content -Path $envFile -Value "`nNGROK_AUTHTOKEN=$authToken"
    } else {
        Write-Host "Authtoken cannot be empty. Exiting." -ForegroundColor Red
        exit 1
    }
}

if (-not $domain) {
    Write-Host "`n[!] Your Free Static Domain (Claim at https://dashboard.ngrok.com/cloud-edge/domains)" -ForegroundColor Yellow
    Write-Host "    Example: emphatic-tackle-monetary.ngrok-free.dev" -ForegroundColor Gray
    $domainInput = Read-Host "Paste your Free Static Domain (optional, press Enter for random URL)"
    if ($domainInput) {
        $domain = $domainInput.Trim().Replace("https://", "").Replace("http://", "").TrimEnd("/")
        Add-Content -Path $envFile -Value "NGROK_DOMAIN=$domain"
    }
}

# 3. Configure Authtoken in ngrok
Write-Host "`n[2/3] Configuring Ngrok authentication..." -ForegroundColor Yellow
& ".\ngrok.exe" config add-authtoken $authToken

# 4. Launch tunnel
Write-Host "`n[3/3] Starting permanent tunnel to local FastAPI on Port 8000..." -ForegroundColor Yellow
if ($domain) {
    Write-Host "`n==========================================================" -ForegroundColor Green
    Write-Host "  SUCCESS! YOUR PERMANENT URL IS: " -ForegroundColor Green
    Write-Host "  https://$domain" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "1. Chrome Extension API Base URL: https://$domain" -ForegroundColor Yellow
    Write-Host "2. Health Check: https://$domain/health" -ForegroundColor Gray
    Write-Host "3. Autocomplete: https://$domain/api/v1/suggestions?q=hello&top_k=5" -ForegroundColor Gray
    Write-Host "4. Keep this terminal open while testing (or use run_background.vbs for background mode).`n" -ForegroundColor Gray

    & ".\ngrok.exe" http --domain=$domain 8000
} else {
    Write-Host "Launching tunnel with automatic public URL..." -ForegroundColor Gray
    & ".\ngrok.exe" http 8000
}
