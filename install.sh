#!/bin/bash
# Linux installer.
set -e
cd "$(dirname "$0")"

clear
echo "═══════════════════════════════════════"
echo "  Claude Pet — Installer (Linux)"
echo "  by Byteflow.bot"
echo "═══════════════════════════════════════"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ Python 3 not found. Install via your package manager."
  read -p "Press Enter to close..."
  exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "✗ Python 3.10+ required (found $PYVER)."
  read -p "Press Enter to close..."
  exit 1
fi
echo "✓ Python $PYVER"

need_cairo=true
python3 -c "import ctypes; ctypes.CDLL('libcairo.so.2')" 2>/dev/null && need_cairo=false
if $need_cairo; then
  echo "→ Cairo library missing — attempting install (sudo password may be needed)…"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y libcairo2 libcairo2-dev pkg-config
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y cairo cairo-devel pkgconf-pkg-config
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm cairo pkgconf
  else
    echo "  Could not auto-install Cairo. Install manually then re-run."
    read -p "Press Enter to close..."
    exit 1
  fi
fi
echo "✓ Cairo available"

if [ ! -d ".venv" ]; then
  echo "→ Creating virtual environment…"
  python3 -m venv .venv
fi

echo "→ Installing claude-pet…"
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e . --quiet

echo "→ Wiring Claude Code hooks…"
.venv/bin/claude-pet install-hooks

echo "→ Starting the pet…"
.venv/bin/claude-pet start

echo
echo "═══════════════════════════════════════"
echo "  ✓ Installed! The pet is now running."
echo "═══════════════════════════════════════"
echo
echo "  Open Claude Code — the pet will react automatically"
echo "  to every tool call, success, error, and notification."
echo
read -p "Press Enter to close…"
