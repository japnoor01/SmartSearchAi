# =============================================================================
# SmartSearch AI -- Remove Windows Startup Shortcut
# =============================================================================

$ErrorActionPreference = "Continue"

$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir "SmartSearchAI.lnk"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- REMOVING STARTUP SHORTCUT" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (Test-Path $shortcutPath) {
    Remove-Item -Path $shortcutPath -Force
    Write-Host "`nStartup shortcut '$shortcutPath' removed successfully." -ForegroundColor Green
} else {
    Write-Host "`nNo startup shortcut found in $startupDir." -ForegroundColor Yellow
}
