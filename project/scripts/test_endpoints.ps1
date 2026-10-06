# =============================================================================
# SmartSearch AI -- Endpoint Test Suite (Local & Public Ngrok)
# =============================================================================

$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
$domain = "emphatic-tackle-monetary.ngrok-free.dev"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^NGROK_DOMAIN\s*=\s*(.+)$') { $domain = $Matches[1].Trim() }
    }
}
$localBase = "http://127.0.0.1:8000"
$publicBase = "https://$domain"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- AUTOMATED ENDPOINT VERIFICATION" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# Test 1: Local FastAPI Health Check
# -----------------------------------------------------------------------------
Write-Host "`n[Test 1] Local Health Check (http://127.0.0.1:8000/health)..." -ForegroundColor Yellow
try {
    $t1 = Invoke-RestMethod -Uri "$localBase/health" -Method Get -TimeoutSec 5
    Write-Host "  PASSED! Status: $($t1.status) | Model Loaded: $($t1.model_loaded) | Device: $($t1.device)" -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $_" -ForegroundColor Red
}

# -----------------------------------------------------------------------------
# Test 2: Local Suggestions Autocomplete
# -----------------------------------------------------------------------------
Write-Host "`n[Test 2] Local Autocomplete (/api/v1/suggestions?q=hello&top_k=5)..." -ForegroundColor Yellow
try {
    $t2 = Invoke-RestMethod -Uri "$localBase/api/v1/suggestions?q=hello&top_k=5" -Method Get -TimeoutSec 5
    Write-Host "  PASSED! Returned $($t2.count) suggestions in $($t2.latency_ms)ms:" -ForegroundColor Green
    foreach ($item in $t2.suggestions) {
        Write-Host "    - $($item.text) (score: $([math]::Round($item.score, 3)))" -ForegroundColor Gray
    }
} catch {
    Write-Host "  FAILED: $_" -ForegroundColor Red
}

# -----------------------------------------------------------------------------
# Test 3: Public Ngrok Health Check
# -----------------------------------------------------------------------------
Write-Host "`n[Test 3] Public Ngrok Health ($publicBase/health)..." -ForegroundColor Yellow
try {
    $headers = @{ "ngrok-skip-browser-warning" = "true" }
    $t3 = Invoke-RestMethod -Uri "$publicBase/health" -Headers $headers -Method Get -TimeoutSec 10
    Write-Host "  PASSED! Public tunnel is reaching FastAPI: Status=$($t3.status) | Model=$($t3.model_loaded)" -ForegroundColor Green
} catch {
    Write-Host "  FAILED: $_" -ForegroundColor Red
}

# -----------------------------------------------------------------------------
# Test 4: Public Ngrok Suggestions Autocomplete
# -----------------------------------------------------------------------------
Write-Host "`n[Test 4] Public Ngrok Suggestions ($publicBase/api/v1/suggestions?q=hello&top_k=5)..." -ForegroundColor Yellow
try {
    $headers = @{ "ngrok-skip-browser-warning" = "true" }
    $t4 = Invoke-RestMethod -Uri "$publicBase/api/v1/suggestions?q=hello&top_k=5" -Headers $headers -Method Get -TimeoutSec 10
    Write-Host "  PASSED! Public tunnel returned $($t4.count) suggestions in $($t4.latency_ms)ms:" -ForegroundColor Green
    foreach ($item in $t4.suggestions) {
        Write-Host "    - $($item.text) (score: $([math]::Round($item.score, 3)))" -ForegroundColor Gray
    }
} catch {
    Write-Host "  FAILED: $_" -ForegroundColor Red
}

# -----------------------------------------------------------------------------
# Test 5: CORS Preflight Verification (Chrome Extension Simulation)
# -----------------------------------------------------------------------------
Write-Host "`n[Test 5] CORS Preflight Check for Chrome Extension..." -ForegroundColor Yellow
try {
    $req = [System.Net.HttpWebRequest]::Create("$publicBase/api/v1/suggestions?q=hello&top_k=5")
    $req.Method = "OPTIONS"
    $req.Headers.Add("Origin", "chrome-extension://abcdefghijklmnop")
    $req.Headers.Add("Access-Control-Request-Method", "GET")
    $req.Headers.Add("Access-Control-Request-Headers", "ngrok-skip-browser-warning")
    $resp = $req.GetResponse()
    $allowOrigin = $resp.Headers["Access-Control-Allow-Origin"]
    $resp.Close()

    if ($allowOrigin -eq "chrome-extension://abcdefghijklmnop") {
        Write-Host "  PASSED! Access-Control-Allow-Origin: $allowOrigin" -ForegroundColor Green
    } else {
        Write-Host "  FAILED: Unexpected origin: $allowOrigin" -ForegroundColor Red
    }
} catch {
    Write-Host "  FAILED: $_" -ForegroundColor Red
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host "  ALL TESTS COMPLETE" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
