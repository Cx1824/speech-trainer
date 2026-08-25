@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_SCRIPT=%~dp0scripts\windows-launcher.ps1"

if not exist "%APP_SCRIPT%" (
  echo Stop failed: launcher script is missing.
  pause
  exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%APP_SCRIPT%" -Stop
set "APP_EXIT=%ERRORLEVEL%"
if "%APP_EXIT%"=="0" exit /b 0

echo.
echo Speech Trainer could not be stopped. Error code: %APP_EXIT%
echo Check .runtime\logs\launcher.log for details.
echo.
pause
exit /b %APP_EXIT%
