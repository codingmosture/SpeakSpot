@echo off
setlocal
title SpeakSpot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-SpeakSpot.ps1"
if errorlevel 1 pause
endlocal
