#!/bin/bash
# Double-click on macOS to install Claude Pet.
# Right-click → Open the first time (Gatekeeper blocks plain double-click on unsigned files).
#
# The venv is created at ~/.claude-pet-venv/ — OUTSIDE Desktop / Documents /
# Downloads so macOS TCC (Privacy & Security) never blocks the hooks.

set -e
cd "$(dirname "$0")"
SRC_DIR="$(pwd)"

# Every subshell inherits a safe CWD, so nothing hits TCC-blocked folders.
cd "$HOME"

clear
echo "═══════════════════════════════════════"
echo "  Claude Pet — Installer (macOS)"
echo "  by Byteflow.bot"
echo "═══════════════════════════════════════"
echo

# 1. Python 3.10+
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

# 2. Homebrew (auto-install if missing) + Cairo
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

# 3. Virtual environment — always at ~/.claude-pet-venv/, never in the source tree.
VENV_DIR="$HOME/.claude-pet-venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "→ Creating virtual environment at $VENV_DIR…"
  python3 -m venv "$VENV_DIR"
fi
echo "✓ Virtual environment: $VENV_DIR"

# 4. Install package from the source tree we were run from.
echo "→ Installing claude-pet…"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$SRC_DIR"'[macos]' --quiet
echo "✓ claude-pet installed"

# 5. Wire Claude Code hooks — uses the venv's Python, TCC-safe.
echo "→ Wiring Claude Code hooks…"
"$VENV_DIR/bin/claude-pet" install-hooks

# 6. Start the pet.
echo "→ Starting the pet…"
"$VENV_DIR/bin/claude-pet" start

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
echo "  To diagnose issues later, run:  claude-pet doctor"
echo
read -p "Press Enter to close this window…"
