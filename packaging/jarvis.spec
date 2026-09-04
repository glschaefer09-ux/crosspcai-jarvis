# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — one binary per platform, all roles inside it.

The same spec builds:
    Windows   dist/JARVIS/JARVIS.exe   (windowed, icon.ico)
    Linux     dist/JARVIS/jarvis       (paired with jarvis.desktop)
    macOS     dist/JARVIS.app          (bundle, icon.icns)

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
if IS_WIN and (ROOT / "jarvis/assets/icon.ico").exists():
    icon = str(ROOT / "jarvis/assets/icon.ico")
elif IS_MAC and (ROOT / "jarvis/assets/icon.icns").exists():
    icon = str(ROOT / "jarvis/assets/icon.icns")

a = Analysis(
    # entry.py, not jarvis/__main__.py: PyInstaller compiles its entry script
    # as a top-level module, which would break the package's relative imports.
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "jarvis" / "ui"), "jarvis/ui"),
        (str(ROOT / "jarvis" / "assets"), "jarvis/assets"),
    ],
    hiddenimports=[
        # Roles the supervisor launches lazily, so static analysis misses them.
        "jarvis.services.hermes_svc",
        "jarvis.services.sandbox_svc",
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
    name="JARVIS" if (IS_WIN or IS_MAC) else "jarvis",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # no console window — this is a GUI product
    icon=icon,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="JARVIS",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="JARVIS.app",
        icon=icon,
        bundle_identifier="ai.crosspc.jarvis",
        info_plist={
            "CFBundleName": "JARVIS",
            "CFBundleDisplayName": "JARVIS",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            # Keeps running in the tray when the window is closed.
            "LSUIElement": False,
        },
    )
