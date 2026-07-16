#!/bin/bash
# Build Claude Pet.app on macOS via PyInstaller.
set -e
cd "$(dirname "$0")"

clear
echo "═══════════════════════════════════════"
echo "  Building Claude Pet.app (macOS)"
echo "  by Byteflow.bot"
echo "═══════════════════════════════════════"
echo

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e ".[macos]" --quiet
.venv/bin/pip install pyinstaller --quiet

ASSETS="src/claude_pet/assets"
if [ ! -f "$ASSETS/icon.icns" ]; then
  echo "→ Generating icon.icns…"
  ICONSET="$ASSETS/icon.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  cp "$ASSETS/icon_16.png"   "$ICONSET/icon_16x16.png"
  cp "$ASSETS/icon_32.png"   "$ICONSET/icon_16x16@2x.png"
  cp "$ASSETS/icon_32.png"   "$ICONSET/icon_32x32.png"
  cp "$ASSETS/icon_64.png"   "$ICONSET/icon_32x32@2x.png"
  cp "$ASSETS/icon_128.png"  "$ICONSET/icon_128x128.png"
  cp "$ASSETS/icon_256.png"  "$ICONSET/icon_128x128@2x.png"
  cp "$ASSETS/icon_256.png"  "$ICONSET/icon_256x256.png"
  cp "$ASSETS/icon_512.png"  "$ICONSET/icon_256x256@2x.png"
  cp "$ASSETS/icon_512.png"  "$ICONSET/icon_512x512.png"
  cp "$ASSETS/icon_1024.png" "$ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$ICONSET" -o "$ASSETS/icon.icns"
  rm -rf "$ICONSET"
fi

rm -rf build dist
echo "→ Running PyInstaller…"
.venv/bin/pyinstaller --noconfirm claude_pet.spec

echo
echo "═══════════════════════════════════════"
echo "  ✓ Built: dist/Claude Pet.app"
echo "═══════════════════════════════════════"
echo
read -p "Press Enter to close…"
