#!/bin/bash
# Double-click on macOS to install Claude Pet.
# Right-click → Open the first time (Gatekeeper blocks plain double-click on unsigned files).

set -e
cd "$(dirname "$0")"

clear
echo "═══════════════════════════════════════"
echo "  Claude Pet — Installer (macOS)"
echo "  by Byteflow.bot"
echo "═══════════════════════════════════════"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ Python 3 not found."
  echo "  Install from https://python.org or run:  brew install python"
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

if ! command -v brew >/dev/null 2>&1; then
  echo "→ Homebrew not found. Installing (one-time)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "✗ Homebrew install failed. See https://brew.sh and re-run this."
  read -p "Press Enter to close..."
  exit 1
fi
echo "✓ Homebrew available"
if ! brew list cairo >/dev/null 2>&1; then
  echo "→ Installing Cairo (one-time)…"
  brew install cairo pkg-config
fi
echo "✓ Cairo installed"

if [ ! -d ".venv" ]; then
  echo "→ Creating virtual environment…"
  python3 -m venv .venv
fi
echo "✓ Virtual environment ready"

echo "→ Installing claude-pet…"
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e ".[macos]" --quiet
echo "✓ claude-pet installed"

echo "→ Wiring Claude Code hooks…"
.venv/bin/claude-pet install-hooks

echo "→ Starting the pet…"
.venv/bin/claude-pet start

echo
echo "═══════════════════════════════════════"
echo "  ✓ Installed! The pet is now running."
echo "═══════════════════════════════════════"
echo
echo "  Look at the bottom-right of your screen."
echo
echo "  Open Claude Code — the pet will react automatically"
echo "  to every tool call, success, error, and notification."
echo
read -p "Press Enter to close this window…"
