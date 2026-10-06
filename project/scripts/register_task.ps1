# =============================================================================
# SmartSearch AI -- Windows Task Scheduler Registration
# =============================================================================
# Registers a scheduled task named 'SmartSearchAI_Background' that:
#   - Starts automatically when the current user logs into Windows.
#   - Runs run_background.vbs silently via wscript.exe (zero visible windows).
#   - Automatically restarts if closed.
#   - Keeps running without requiring VS Code or terminal windows.
# =============================================================================

$ErrorActionPreference = "Continue"

$taskName = "SmartSearchAI_Background"
$projectRoot = Split-Path $PSScriptRoot -Parent
$vbsPath = Join-Path $projectRoot "run_background.vbs"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SMARTSEARCH AI -- WINDOWS TASK SCHEDULER SETUP" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (-not (Test-Path $vbsPath)) {
    Write-Host "[ERROR] Could not find run_background.vbs at: $vbsPath" -ForegroundColor Red
    exit 1
}

Write-Host "`nTarget script: $vbsPath" -ForegroundColor Gray
Write-Host "Task name:     $taskName" -ForegroundColor Gray
Write-Host "Trigger:       At User Logon (Automatic)" -ForegroundColor Gray

# Check elevation
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "`n[NOTE] Windows Task Scheduler registration typically requires Administrator privileges." -ForegroundColor Yellow
    Write-Host "       If this fails, right-click 'register_task.bat' and choose 'Run as Administrator'," -ForegroundColor Yellow
    Write-Host "       or run 'scripts\install_startup_shortcut.bat' (zero admin rights needed).`n" -ForegroundColor Yellow
}

$wscript = "$env:SystemRoot\System32\wscript.exe"
$registered = $false

# Method 1: PowerShell ScheduledTask module (most robust on Windows 10/11)
try {
    Write-Host "Registering task via PowerShell ScheduledTasks cmdlets..." -ForegroundColor Yellow
    $action = New-ScheduledTaskAction -Execute $wscript -Argument "`"$vbsPath`"" -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0)
    
    if ($isAdmin) {
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    } else {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop | Out-Null
    }
    $registered = $true
    Write-Host "Task registered successfully via ScheduledTasks cmdlets!" -ForegroundColor Green
} catch {
    Write-Host "PowerShell cmdlet registration failed: $_" -ForegroundColor DarkGray
    Write-Host "Attempting schtasks.exe fallback..." -ForegroundColor Yellow
}

# Method 2: schtasks.exe fallback
if (-not $registered) {
    # Check if task already exists
    $existing = schtasks.exe /query /tn "$taskName" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Existing task '$taskName' detected. Replacing..." -ForegroundColor Yellow
        schtasks.exe /delete /tn "$taskName" /f | Out-Null
    }

    $actionCmd = "`"$wscript`" `"$vbsPath`""
    $createOutput = if ($isAdmin) {
        schtasks.exe /create /tn "$taskName" /tr "$actionCmd" /sc onlogon /rl highest /f 2>&1
    } else {
        schtasks.exe /create /tn "$taskName" /tr "$actionCmd" /sc onlogon /f 2>&1
    }

    if ($LASTEXITCODE -eq 0) {
        $registered = $true
        Write-Host "Task registered successfully via schtasks.exe!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Could not register scheduled task: $createOutput" -ForegroundColor Red
        Write-Host "`nRECOMMENDED FIX:" -ForegroundColor Cyan
        Write-Host "1. Right-click 'scripts\register_task.bat' and select 'Run as administrator'" -ForegroundColor Yellow
        Write-Host "2. OR run 'scripts\install_startup_shortcut.bat' (works for all users without admin rights)`n" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "`nTesting execution of the scheduled task..." -ForegroundColor Yellow
schtasks.exe /run /tn "$taskName" | Out-Null
Start-Sleep -Seconds 3

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "  SETUP COMPLETE! TASK SCHEDULER IS CONFIGURED" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "1. SmartSearch AI will now start automatically whenever your PC logs on."
Write-Host "2. VS Code and terminal windows are no longer required."
Write-Host "3. To check running status at any time, run: status_services.bat"
Write-Host "4. To stop services at any time, run:        stop_services.bat"
Write-Host "5. To remove this scheduled task, run:      scripts\unregister_task.bat`n"
