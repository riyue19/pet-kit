@echo off
rem Double-click to create the desktop shortcut for the pet (runs create-shortcut.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-shortcut.ps1"
if errorlevel 1 (
    echo.
    echo Failed to create the shortcut. See the message above.
)
echo.
pause
