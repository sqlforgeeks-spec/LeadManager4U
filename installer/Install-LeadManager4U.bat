@echo off
title LeadManager4U.ai - Setup Wizard
color 0D

echo.
echo   ===============================================
echo       LeadManager4U.ai - Setup Wizard
echo   ===============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "PS_GUI=%SCRIPT_DIR%Setup-GUI.ps1"

if not exist "%PS_GUI%" (
    color 0C
    echo   ERROR: Setup-GUI.ps1 not found!
    echo   Expected at: %PS_GUI%
    echo.
    pause
    exit /b 1
)

echo   Launching graphical installer with administrator privileges...
echo   (Click 'Yes' on the UAC prompt if it appears)
echo.

powershell -ExecutionPolicy Bypass -Command ^
    "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%PS_GUI%\"' -Verb RunAs"

exit /b 0
