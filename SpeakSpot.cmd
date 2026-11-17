@echo off
setlocal
title SpeakSpot
python "%~dp0start.py"
if errorlevel 1 pause
endlocal
