# PyInstaller spec for Claude Pet. Builds a standalone executable per platform.
# ruff: noqa
# flake8: noqa

import sys
from pathlib import Path

BLOCK_CIPHER = None
HERE = Path(SPECPATH).resolve()
SRC = HERE / "src" / "claude_pet"
ASSETS = SRC / "assets"

datas = [(str(ASSETS), "claude_pet/assets")]

if sys.platform == "darwin":
    icon_path = str(ASSETS / "icon.icns") if (ASSETS / "icon.icns").exists() else str(ASSETS / "icon_1024.png")
elif sys.platform == "win32":
    icon_path = str(ASSETS / "icon.ico")
else:
    icon_path = str(ASSETS / "icon_256.png")

a = Analysis(
    [str(SRC / "__main__.py")],
    pathex=[str(HERE / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "claude_pet", "claude_pet.app", "claude_pet.bot_svg",
        "claude_pet.cli", "claude_pet.hook", "claude_pet.server",
        "cairosvg", "cairocffi", "flask", "flask_cors", "PIL", "requests",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=BLOCK_CIPHER,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=BLOCK_CIPHER)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="claude-pet",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    upx_exclude=[], runtime_tmpdir=None, console=False, icon=icon_path,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe, name="Claude Pet.app", icon=icon_path,
        bundle_identifier="bot.byteflow.claudepet",
        info_plist={
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.3.0",
            "CFBundleVersion": "0.3.0",
            "NSHumanReadableCopyright": "© 2026 Byteflow.bot",
        },
    )
