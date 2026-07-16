@echo off
:: Double-click on Windows to install Claude Pet.
:: Venv lives at %USERPROFILE%\.claude-pet-venv\ — never under the source tree.
setlocal enabledelayedexpansion
set SRC_DIR=%~dp0
set SRC_DIR=%SRC_DIR:~0,-1%
cd /d "%USERPROFILE%"

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

set VENV_DIR=%USERPROFILE%\.claude-pet-venv
if not exist "%VENV_DIR%" (
  echo  -- Creating virtual environment at %VENV_DIR%...
  python -m venv "%VENV_DIR%"
)
echo OK Virtual environment: %VENV_DIR%

echo  -- Installing claude-pet...
"%VENV_DIR%\Scripts\python" -m pip install --upgrade pip --quiet
"%VENV_DIR%\Scripts\python" -m pip install -e "%SRC_DIR%" --quiet
echo OK claude-pet installed

echo  -- Wiring Claude Code hooks...
"%VENV_DIR%\Scripts\claude-pet" install-hooks

echo  -- Starting the pet...
"%VENV_DIR%\Scripts\claude-pet" start

echo.
echo =====================================
echo   Installed! The pet is now running.
echo =====================================
echo.
echo   Open Claude Code -- the pet will react automatically.
echo   To diagnose later:  claude-pet doctor
echo.
pause
