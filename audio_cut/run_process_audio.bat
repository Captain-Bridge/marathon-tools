@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%process_audio.ps1"

if not exist "%PS_SCRIPT%" (
    echo Cannot find PowerShell script:
    echo %PS_SCRIPT%
    pause
    exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Processing failed. Exit code: %EXIT_CODE%
    pause
    exit /b %EXIT_CODE%
)

echo.
echo Processing complete.
pause
