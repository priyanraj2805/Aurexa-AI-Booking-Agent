@echo off
setlocal

REM Use the PowerShell startup helper so URL parsing logic stays in one place.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"

endlocal
