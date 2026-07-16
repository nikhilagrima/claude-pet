@echo off
:: Double-click on Windows to install Claude Pet.
setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo =====================================
echo   Claude Pet -- Windows Installer
echo   by Byteflow.bot
echo =====================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo X Python not found.
  echo   Install from https://www.python.org/downloads/ then re-run.
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo OK Python !PYVER!

if not exist .venv (
  echo  -- Creating virtual environment...
  python -m venv .venv
)
echo OK Virtual environment ready

echo  -- Installing claude-pet...
".venv\Scripts\python" -m pip install --upgrade pip --quiet
".venv\Scripts\python" -m pip install -e . --quiet
echo OK claude-pet installed

echo  -- Wiring Claude Code hooks...
".venv\Scripts\claude-pet" install-hooks

echo  -- Starting the pet...
".venv\Scripts\claude-pet" start

echo.
echo =====================================
echo   Installed! The pet is now running.
echo =====================================
echo.
echo   Look at the bottom-right of your screen.
echo.
echo   Open Claude Code -- the pet will react automatically
echo   to every tool call, success, error, and notification.
echo.
pause
