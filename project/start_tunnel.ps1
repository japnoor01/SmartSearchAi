# =============================================================================
# SmartSearch AI -- Cloudflare Tunnel Launcher
# =============================================================================
# Launches an encrypted Cloudflare Tunnel connected to your local Nginx proxy.
# Generates a public HTTPS URL (e.g. https://xxx.trycloudflare.com) so anyone
# using the Chrome Extension can access your SmartSearch backend from anywhere.
# =============================================================================

$ErrorActionPreference = "Continue"

# Automatically set working directory to this script's directory
Set-Location -Path $PSScriptRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- CLOUDFLARE TUNNEL LAUNCHER" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Docker status
try {
    $dockerCheck = docker ps 2>&1
} catch {
    Write-Host "[ERROR] Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}

# 2. Ensure smartsearch-api and nginx are running
Write-Host "`n[1/3] Ensuring SmartSearch API & Nginx are running..." -ForegroundColor Yellow
docker compose up -d nginx smartsearch-api

# 3. Launch cloudflared tunnel
Write-Host "`n[2/3] Starting Cloudflare Tunnel container..." -ForegroundColor Yellow
docker compose --profile tunnel up -d cloudflared

Write-Host "Waiting 5 seconds for Cloudflare edge registration..." -ForegroundColor Gray
Start-Sleep -Seconds 6

# 4. Extract public URL from logs
Write-Host "`n[3/3] Retrieving your public HTTPS endpoint..." -ForegroundColor Yellow
$logRaw = (docker logs smartsearch-tunnel 2>&1) | Out-String

$matchedUrl = $null
if ($logRaw -match "(https://[a-zA-Z0-9-]+\.trycloudflare\.com)") {
    $matchedUrl = $Matches[1]
}

Write-Host "`n==========================================================" -ForegroundColor Green
if ($matchedUrl) {
    Write-Host "  SUCCESS! YOUR PUBLIC CLOUDFLARE URL IS LIVE:" -ForegroundColor Green
    Write-Host "  $matchedUrl" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "`nNext steps to use with your Chrome Extension:" -ForegroundColor Yellow
    Write-Host "  1. Click the SmartSearch AI extension icon in Chrome."
    Write-Host "  2. In the 'API Base URL' field, paste:" -NoNewline
    Write-Host " $matchedUrl" -ForegroundColor Cyan
    Write-Host "  3. Click 'Save Settings'."
    Write-Host "  4. Status banner will display: 'FastAPI Online (CPU)'!"
    Write-Host "`nAnyone with this extension can now use your server from any browser/network."
} else {
    Write-Host "  Cloudflare tunnel container is running." -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "If using a custom domain token (CLOUDFLARE_TUNNEL_TOKEN), your permanent domain is active."
    Write-Host "Run 'docker logs smartsearch-tunnel' to inspect live status."
}

Write-Host "`nTo stop the tunnel at any time, run:" -ForegroundColor Gray
Write-Host "  docker compose --profile tunnel stop cloudflared`n" -ForegroundColor Gray
