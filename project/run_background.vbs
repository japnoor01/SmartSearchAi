' =============================================================================
' SmartSearch AI -- Windows Background Runner (VBScript Launcher)
' =============================================================================
' Launches run_background.ps1 with completely hidden window style (0, False).
' Double-clicking this file or calling it from Windows Task Scheduler starts
' both FastAPI and ngrok silently with zero command prompt or terminal windows.
' =============================================================================

Set WshShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir
psCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptDir & "\run_background.ps1"""
WshShell.Run psCommand, 0, False
Set WshShell = Nothing
