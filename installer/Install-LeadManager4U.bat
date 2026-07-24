@echo off
title LeadManager4U.ai - Installer
color 0D

echo.
echo   ===============================================
echo       LeadManager4U.ai - One-Click Installer
echo   ===============================================
echo.

:: Locate the PowerShell script next to this .bat file
set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%Setup-LeadManager4U.ps1"

if not exist "%PS_SCRIPT%" (
    color 0C
    echo   ERROR: Setup-LeadManager4U.ps1 not found!
    echo   Expected at: %PS_SCRIPT%
    echo.
    pause
    exit /b 1
)

echo   Launching installer with administrator privileges...
echo   (Click 'Yes' on the UAC prompt if it appears)
echo.

:: Launch PowerShell elevated — the -File flag runs the .ps1 script
powershell -ExecutionPolicy Bypass -Command ^
    "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%PS_SCRIPT%\"' -Verb RunAs"

exit /b 0
