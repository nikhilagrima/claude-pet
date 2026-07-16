@echo off
:: Build standalone claude-pet.exe on Windows via PyInstaller.
setlocal enabledelayedexpansion
cd /d "%~dp0"

cls
echo =====================================
echo   Building claude-pet.exe (Windows)
echo   by Byteflow.bot
echo =====================================
echo.

if not exist .venv (
  python -m venv .venv
)
".venv\Scripts\python" -m pip install --upgrade pip --quiet
".venv\Scripts\python" -m pip install -e . --quiet
".venv\Scripts\python" -m pip install pyinstaller --quiet

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo  -- Running PyInstaller...
".venv\Scripts\pyinstaller" --noconfirm claude_pet.spec

echo.
echo =====================================
echo   Built: dist\claude-pet.exe
echo =====================================
echo.
pause
