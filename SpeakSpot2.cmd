@echo off
setlocal
title SpeakSpot2
python "%~dp0start.py"
if errorlevel 1 pause
endlocal
