@echo off
title LeadManager4U.ai - Uninstaller
color 0E

echo.
echo   ===============================================
echo       LeadManager4U.ai - Uninstaller
echo   ===============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%Uninstall-LeadManager4U.ps1"

if not exist "%PS_SCRIPT%" (
    color 0C
    echo   ERROR: Uninstall-LeadManager4U.ps1 not found!
    echo   Expected at: %PS_SCRIPT%
    echo.
    pause
    exit /b 1
)

echo   Launching uninstaller with administrator privileges...
echo   (Click 'Yes' on the UAC prompt if it appears)
echo.

powershell -ExecutionPolicy Bypass -Command ^
    "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%PS_SCRIPT%\"' -Verb RunAs"

exit /b 0
