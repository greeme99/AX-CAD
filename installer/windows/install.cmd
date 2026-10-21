@echo off
rem AX-CAD per-PC install (no admin rights needed)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\install.ps1" %*
pause
