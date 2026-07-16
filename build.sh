#!/bin/bash
# Build standalone claude-pet binary on Linux via PyInstaller.
set -e
cd "$(dirname "$0")"

clear
echo "═══════════════════════════════════════"
echo "  Building claude-pet (Linux)"
echo "  by Byteflow.bot"
echo "═══════════════════════════════════════"
echo

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e . --quiet
.venv/bin/pip install pyinstaller --quiet

rm -rf build dist

echo "→ Running PyInstaller…"
.venv/bin/pyinstaller --noconfirm claude_pet.spec

echo
echo "═══════════════════════════════════════"
echo "  ✓ Built: dist/claude-pet"
echo "═══════════════════════════════════════"
echo
read -p "Press Enter to close…"
