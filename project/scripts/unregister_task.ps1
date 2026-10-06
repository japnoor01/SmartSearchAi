# =============================================================================
# SmartSearch AI -- Windows Task Scheduler Unregister
# =============================================================================
# Removes the 'SmartSearchAI_Background' scheduled task from Windows.
# =============================================================================

$ErrorActionPreference = "Continue"
$taskName = "SmartSearchAI_Background"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- REMOVING SCHEDULED TASK" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$existing = schtasks.exe /query /tn "$taskName" 2>&1
if ($LASTEXITCODE -eq 0) {
    schtasks.exe /delete /tn "$taskName" /f | Out-Null
    Write-Host "`nScheduled task '$taskName' has been removed." -ForegroundColor Green
} else {
    Write-Host "`nScheduled task '$taskName' was not found on this system." -ForegroundColor Yellow
}
