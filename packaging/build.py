#!/usr/bin/env python3
"""
build.py — produce the installable artifact for whichever platform you run it on.

    python packaging/build.py              build for this platform
    python packaging/build.py --deb        also produce a .deb (Linux)
    python packaging/build.py --icons      regenerate the icon set only
    python packaging/build.py --sign-key K stamp a licence signing secret in

Every platform gets the same app and the same single icon:

    Windows   dist/JARVAS/JARVAS.exe        + Start Menu shortcut via install.ps1
    Linux     dist/JARVAS/jarvas            + jarvas.desktop (one icon in the launcher)
    macOS     dist/JARVAS.app               + drag to Applications
    TrueNAS   docker image crosspcai/jarvas (packaging/Dockerfile)
    CrossPC AI OS  either the Linux binary or the container
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SYSTEM = platform.system()

# A Windows console defaults to cp1252 and dies on any non-ASCII in a print.
# A build script must never fail over its own progress output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def run(cmd: list[str], **kw) -> int:
    print(">", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT), **kw)


# -- icons ---------------------------------------------------------------------

def build_icons() -> None:
    """One source drawing, every size and container the platforms want."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("! Pillow is not installed — skipping icons (pip install pillow)")
        return

    out = ROOT / "jarvas" / "assets"
    out.mkdir(parents=True, exist_ok=True)

    def draw(size: int) -> "Image.Image":
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        s = size / 256.0
        c = size / 2
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(56 * s),
                            fill=(5, 8, 16, 255))
        d.ellipse([c - 78 * s, c - 78 * s, c + 78 * s, c + 78 * s],
                  outline=(0, 212, 255, 150), width=max(1, int(4 * s)))
        d.ellipse([c - 60 * s, c - 60 * s, c + 60 * s, c + 60 * s],
                  outline=(0, 212, 255, 85), width=max(1, int(2 * s)))
        for r, a in ((46, 35), (42, 65), (38, 115), (34, 255)):
            d.ellipse([c - r * s, c - r * s, c + r * s, c + r * s], fill=(0, 190, 255, a))
        d.ellipse([c - 20 * s, c - 24 * s, c + 4 * s, c], fill=(150, 238, 255, 175))
        w = max(1, int(5 * s))
        for x0, y0, x1, y1 in ((0, -110, 0, -90), (0, 90, 0, 110),
                               (-110, 0, -90, 0), (90, 0, 110, 0)):
            d.line([c + x0 * s, c + y0 * s, c + x1 * s, c + y1 * s],
                   fill=(0, 212, 255, 190), width=w)
        return img

    master = draw(1024)
    master.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
    master.resize((256, 256), Image.LANCZOS).save(out / "icon@256.png")
    master.save(out / "icon@1024.png")
    master.save(out / "icon.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    # macOS .icns needs an iconset directory and the system iconutil.
    if SYSTEM == "Darwin":
        iconset = ROOT / "build" / "JARVAS.iconset"
        iconset.mkdir(parents=True, exist_ok=True)
        for sz in (16, 32, 64, 128, 256, 512):
            master.resize((sz, sz), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}.png")
            master.resize((sz * 2, sz * 2), Image.LANCZOS).save(
                iconset / f"icon_{sz}x{sz}@2x.png")
        run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "icon.icns")])
    print("OK: icons written to", out)


# -- licence signing -----------------------------------------------------------

def stamp_signing_key(secret: str) -> None:
    """Replace the placeholder signing secret before a release build.

    This gates honest customers and keeps entitlements tidy. It is not copy
    protection — anything that costs money to serve must be checked server side.
    """
    lic = ROOT / "jarvas" / "license.py"
    text = lic.read_text(encoding="utf-8")
    marker = 'SIGNING_SECRET = b"'
    start = text.index(marker) + len(marker)
    end = text.index('"', start)
    lic.write_text(text[:start] + secret + text[end:], encoding="utf-8")
    print("OK: signing secret stamped into license.py")


# -- freeze --------------------------------------------------------------------

def _wipe(path: Path) -> None:
    """Remove a build directory, tolerating Windows' transient locks.

    Cloud-sync clients (OneDrive, Google Drive) and antivirus hold freshly
    written files open for seconds at a time, and a project folder inside
    Downloads is very often synced. PyInstaller's own --clean aborts the whole
    build over that. These are caches, so retry a few times and carry on -
    never let a locked file stop a release build.
    """
    import time

    for attempt in range(3):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            time.sleep(0.6 * (attempt + 1))

    if not path.exists():
        return

    # Windows refuses to delete a directory with an open handle but will still
    # rename it. Move it out of the way so the build gets a clean target, then
    # delete the leftovers on a best-effort basis.
    stale = path.with_name(f"{path.name}.stale-{int(time.time())}")
    try:
        path.rename(stale)
    except OSError:
        print(f"! {path} is locked and cannot be moved - close anything using it")
        return
    shutil.rmtree(stale, ignore_errors=True)
    if stale.exists():
        print(f"  (left {stale.name} behind; it can be deleted later)")


def build_binary() -> Path | None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("! PyInstaller is not installed (pip install pyinstaller)")
        return None

    _wipe(DIST)
    _wipe(ROOT / "build" / "jarvas")

    # No --clean: it re-does the wipe above and aborts the whole build if a
    # single cache directory is momentarily locked.
    code = run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                str(ROOT / "packaging" / "jarvas.spec")])
    if code != 0:
        print("! PyInstaller failed")
        return None

    target = DIST / ("JARVAS.app" if SYSTEM == "Darwin" else "JARVAS")
    print(f"OK: built {target}")
    return target


# -- linux .deb ----------------------------------------------------------------

def build_deb() -> None:
    """A .deb so `apt install ./jarvas.deb` puts one icon in the launcher."""
    if SYSTEM != "Linux":
        print("! .deb packaging only runs on Linux")
        return
    staged = DIST / "JARVAS"
    if not staged.exists():
        print("! build the binary first")
        return

    pkg = ROOT / "build" / "deb" / "jarvas_1.0.0_amd64"
    shutil.rmtree(pkg, ignore_errors=True)
    (pkg / "DEBIAN").mkdir(parents=True)
    (pkg / "opt" / "jarvas").mkdir(parents=True)
    (pkg / "usr" / "bin").mkdir(parents=True)
    (pkg / "usr" / "share" / "applications").mkdir(parents=True)
    (pkg / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps").mkdir(parents=True)

    shutil.copytree(staged, pkg / "opt" / "jarvas", dirs_exist_ok=True)
    shutil.copy(ROOT / "jarvas" / "assets" / "icon.png",
                pkg / "usr/share/icons/hicolor/512x512/apps/jarvas.png")
    shutil.copy(ROOT / "packaging" / "jarvas.desktop",
                pkg / "usr/share/applications/jarvas.desktop")

    (pkg / "DEBIAN" / "control").write_text(
        "Package: jarvas\n"
        "Version: 1.0.0\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Architecture: amd64\n"
        "Maintainer: CCD Enterprise and Development <support@crosspcai.com>\n"
        "Description: JARVAS — the CrossPCAI control centre\n"
        " Chat, agents, sandbox and Slack in one app. Runs your background\n"
        " services for you and manages every machine you install it on.\n",
        encoding="utf-8")

    (pkg / "DEBIAN" / "postinst").write_text(
        "#!/bin/sh\nset -e\n"
        "ln -sf /opt/jarvas/jarvas /usr/bin/jarvas\n"
        "chmod +x /opt/jarvas/jarvas\n"
        "update-desktop-database >/dev/null 2>&1 || true\n"
        "gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true\n",
        encoding="utf-8")
    os.chmod(pkg / "DEBIAN" / "postinst", 0o755)

    (pkg / "DEBIAN" / "prerm").write_text(
        "#!/bin/sh\nset -e\nrm -f /usr/bin/jarvas\n", encoding="utf-8")
    os.chmod(pkg / "DEBIAN" / "prerm", 0o755)

    out = DIST / "jarvas_1.0.0_amd64.deb"
    if run(["dpkg-deb", "--build", "--root-owner-group", str(pkg), str(out)]) == 0:
        print(f"OK: {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--icons", action="store_true", help="regenerate icons and stop")
    ap.add_argument("--deb", action="store_true", help="also build a .deb (Linux)")
    ap.add_argument("--sign-key", help="stamp the licence signing secret before building")
    args = ap.parse_args()

    if args.sign_key:
        stamp_signing_key(args.sign_key)
    build_icons()
    if args.icons:
        return 0
    if build_binary() is None:
        return 1
    if args.deb:
        build_deb()

    print("\nNext:")
    if SYSTEM == "Windows":
        print("  powershell -ExecutionPolicy Bypass -File install.ps1")
    elif SYSTEM == "Darwin":
        print("  cp -R dist/JARVAS.app /Applications/")
    else:
        print("  sudo ./install.sh          # or: sudo apt install ./dist/jarvas_1.0.0_amd64.deb")
    return 0


if __name__ == "__main__":
    sys.exit(main())
