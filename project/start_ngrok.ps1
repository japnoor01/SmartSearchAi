# =============================================================================
# SmartSearch AI -- Permanent Ngrok Tunnel Launcher
# =============================================================================
# Connects local Docker Nginx proxy (Port 80) to your free static Ngrok domain.
# The URL never changes, never expires, and has zero file uploads.
# =============================================================================

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- PERMANENT NGROK LAUNCHER" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Ensure Docker backend containers are running
Write-Host "`n[1/3] Checking SmartSearch Docker backend..." -ForegroundColor Yellow
$apiStatus = docker ps --filter "name=smartsearch-api" --filter "status=running" -q
if (-not $apiStatus) {
    Write-Host "Starting Docker containers (smartsearch-api + nginx)..." -ForegroundColor Gray
    docker compose up -d nginx smartsearch-api
} else {
    Write-Host "Docker backend is running and healthy (Port 80)." -ForegroundColor Green
}

# 2. Check for .env credentials
$envFile = Join-Path $PSScriptRoot ".env"
$authToken = $null
$domain = $null

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*NGROK_AUTHTOKEN\s*=\s*(.+)$') { $authToken = $Matches[1].Trim() }
        if ($_ -match '^\s*NGROK_DOMAIN\s*=\s*(.+)$') { $domain = $Matches[1].Trim() }
    }
}

# Prompt if missing
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
    Write-Host "    Example: smartsearch-ai.ngrok-free.app (or press Enter for automatic random URL)" -ForegroundColor Gray
    $domainInput = Read-Host "Paste your Free Static Domain (optional)"
    if ($domainInput) {
        $domain = $domainInput.Trim().Replace("https://", "").Replace("http://", "").TrimEnd("/")
        Add-Content -Path $envFile -Value "NGROK_DOMAIN=$domain"
    }
}

# 3. Configure Authtoken in ngrok
Write-Host "`n[2/3] Configuring Ngrok authentication..." -ForegroundColor Yellow
& ".\ngrok.exe" config add-authtoken $authToken

# 4. Launch tunnel
Write-Host "`n[3/3] Starting permanent tunnel to local Nginx on Port 80..." -ForegroundColor Yellow
if ($domain) {
    Write-Host "`n==========================================================" -ForegroundColor Green
    Write-Host "  SUCCESS! YOUR PERMANENT URL IS: " -ForegroundColor Green
    Write-Host "  https://$domain" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "1. Paste this URL into your Chrome Extension: https://$domain" -ForegroundColor Yellow
    Write-Host "2. Click 'Save Settings' in popup."
    Write-Host "3. Keep this window open while using the extension!`n" -ForegroundColor Gray

    & ".\ngrok.exe" http 80 --domain=$domain
} else {
    Write-Host "Launching tunnel with automatic public URL..." -ForegroundColor Gray
    & ".\ngrok.exe" http 80
}
