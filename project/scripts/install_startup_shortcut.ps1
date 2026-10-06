# =============================================================================
# SmartSearch AI -- Install Windows Startup Shortcut (Zero Admin Rights Required)
# =============================================================================
# Places a silent shortcut in your Windows User Startup folder:
# shell:startup (C:\Users\<User>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup)
#
# Whenever you turn on your PC and log in, SmartSearch AI starts automatically
# in the background without needing VS Code, open terminals, or administrator rights.
# =============================================================================

$ErrorActionPreference = "Continue"

$projectRoot = Split-Path $PSScriptRoot -Parent
$vbsPath = Join-Path $projectRoot "run_background.vbs"
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir "SmartSearchAI.lnk"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- WINDOWS STARTUP SHORTCUT SETUP" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (-not (Test-Path $vbsPath)) {
    Write-Host "[ERROR] Could not find run_background.vbs at: $vbsPath" -ForegroundColor Red
    exit 1
}

Write-Host "`nTarget script:    $vbsPath" -ForegroundColor Gray
Write-Host "Startup directory: $startupDir" -ForegroundColor Gray
Write-Host "Shortcut file:     $shortcutPath" -ForegroundColor Gray

try {
    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
    $shortcut.Arguments = "`"$vbsPath`""
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.WindowStyle = 7 # Minimized
    $shortcut.Description = "SmartSearch AI Background Supervisor (FastAPI + Ngrok)"
    $shortcut.Save()

    Write-Host "`n==========================================================" -ForegroundColor Green
    Write-Host "  SUCCESS! STARTUP SHORTCUT INSTALLED" -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "1. SmartSearch AI will now launch silently on every Windows login."
    Write-Host "2. No administrator privileges were required."
    Write-Host "3. To remove from startup at any time, run: scripts\remove_startup_shortcut.bat"
    Write-Host "4. To check running services at any time, run: status_services.bat`n"
} catch {
    Write-Host "[ERROR] Failed to create startup shortcut: $_" -ForegroundColor Red
    exit 1
}
