@echo off
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%reset-demo.ps1" 2>nul
  if errorlevel 9009 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%reset-demo.ps1"
  )
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%reset-demo.ps1"
)
echo.
pause
