@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_RUNTIME=%~dp0.runtime"
set "APP_LOGS=%~dp0.runtime\logs"
set "APP_SCRIPT=%~dp0scripts\windows-launcher.ps1"
set "APP_STATUS=%~dp0.runtime\launch.success"
set "APP_BOOT_LOG=%~dp0.runtime\logs\launcher-bootstrap.log"

if not exist "%APP_LOGS%" mkdir "%APP_LOGS%" >nul 2>&1
if exist "%APP_STATUS%" del /q "%APP_STATUS%" >nul 2>&1

> "%APP_BOOT_LOG%" echo [%date% %time%] Speech Trainer launcher started.

if not exist "%APP_SCRIPT%" (
  >> "%APP_BOOT_LOG%" echo Launcher script is missing: %APP_SCRIPT%
  set "APP_EXIT=2"
  goto APP_FAILED
)

where powershell.exe >nul 2>&1
if errorlevel 1 (
  >> "%APP_BOOT_LOG%" echo Windows PowerShell was not found.
  set "APP_EXIT=3"
  goto APP_FAILED
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%APP_SCRIPT%"
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" goto APP_FAILED
if not exist "%APP_STATUS%" (
  >> "%APP_BOOT_LOG%" echo PowerShell ended without a success marker.
  set "APP_EXIT=4"
  goto APP_FAILED
)

exit /b 0

:APP_FAILED
echo.
echo Speech Trainer failed to start. Error code: %APP_EXIT%
echo The window will stay open so the error can be inspected.
echo Diagnostic logs:
echo   %APP_BOOT_LOG%
echo   %APP_LOGS%\launcher.log
echo   %APP_LOGS%\backend-error.log
echo.
echo Please send the three log files above to the package provider.
echo Do not send backend\.env because it contains the private API key.
echo.
pause
exit /b %APP_EXIT%
