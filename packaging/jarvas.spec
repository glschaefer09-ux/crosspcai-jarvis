# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — one binary per platform, all roles inside it.

The same spec builds:
    Windows   dist/JARVAS/JARVAS.exe   (windowed, icon.ico)
    Linux     dist/JARVAS/jarvas       (paired with jarvas.desktop)
    macOS     dist/JARVAS.app          (bundle, icon.icns)

Built one-dir rather than one-file: the supervisor re-launches this same
executable with --service, and a one-file build would unpack the whole archive
again for every daemon.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

icon = None
if IS_WIN and (ROOT / "jarvas/assets/icon.ico").exists():
    icon = str(ROOT / "jarvas/assets/icon.ico")
elif IS_MAC and (ROOT / "jarvas/assets/icon.icns").exists():
    icon = str(ROOT / "jarvas/assets/icon.icns")

a = Analysis(
    # entry.py, not jarvas/__main__.py: PyInstaller compiles its entry script
    # as a top-level module, which would break the package's relative imports.
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "jarvas" / "ui"), "jarvas/ui"),
        (str(ROOT / "jarvas" / "assets"), "jarvas/assets"),
    ],
    hiddenimports=[
        # Roles the supervisor launches lazily, so static analysis misses them.
        "jarvas.services.hermes_svc",
        "jarvas.services.sandbox_svc",
        # Optional at runtime; included when present so the desktop shell works.
        "webview", "pystray", "PIL.Image", "PIL.ImageDraw",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="JARVAS" if (IS_WIN or IS_MAC) else "jarvas",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # no console window — this is a GUI product
    icon=icon,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="JARVAS",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="JARVAS.app",
        icon=icon,
        bundle_identifier="ai.crosspc.jarvas",
        info_plist={
            "CFBundleName": "JARVAS",
            "CFBundleDisplayName": "JARVAS",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            # Keeps running in the tray when the window is closed.
            "LSUIElement": False,
        },
    )
