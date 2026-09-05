#!/usr/bin/env python3
"""
desktop.py — the window and the tray icon behind the single app icon.

Three levels of graceful degradation, because the same binary has to open
something usable on a Windows laptop, a GNOME desktop, a Mac, and a minimal
Linux box with no GUI toolkit at all:

  1. pywebview  native window - the real product
  2. browser    system browser pointed at the local server
  3. headless   print the URL and keep serving

The tray icon (pystray) is optional on top of any of those: close the window
and JARVAS keeps running with its agents alive, one click from the tray.
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import config

ASSETS = Path(__file__).parent / "assets"


def _icon_path() -> Path | None:
    for name in ("icon.png", "icon.ico"):
        p = ASSETS / name
        if p.exists():
            return p
    return None


def _make_icon_image():
    """Load the app icon, or draw a serviceable one if Pillow is present.

    A missing icon must never stop the app from opening - it falls back to
    no tray rather than crashing on launch.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    p = _icon_path()
    if p and p.suffix == ".png":
        try:
            return Image.open(p)
        except OSError:
            pass

    img = Image.new("RGBA", (64, 64), (5, 8, 16, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), outline=(0, 212, 255, 255), width=3)
    d.ellipse((22, 22, 42, 42), fill=(0, 212, 255, 255))
    return img


def _start_tray(url: str, on_quit) -> "object | None":
    try:
        import pystray
    except ImportError:
        return None
    image = _make_icon_image()
    if image is None:
        return None

    menu = pystray.Menu(
        pystray.MenuItem("Open JARVAS", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem("Status", lambda *_: webbrowser.open(url + "/#/system")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, *_: (icon.stop(), on_quit())),
    )
    icon = pystray.Icon("jarvas", image, f"{config.APP_NAME} {config.VERSION}", menu)
    threading.Thread(target=icon.run, daemon=True, name="jarvas-tray").start()
    return icon


def launch(app, prefer_browser: bool = False) -> int:
    """Serve locally, then open the best window this machine can give us."""
    from .server import serve

    port = app.cfg.get("ui_port", 5580)
    bind = app.cfg.get("bind", "127.0.0.1")
    serve(app, background=True)
    url = f"http://{'127.0.0.1' if bind == '0.0.0.0' else bind}:{port}"

    stopping = threading.Event()

    def quit_app():
        stopping.set()
        app.shutdown()

    tray = _start_tray(url, quit_app)

    if not prefer_browser:
        try:
            import webview  # pywebview
        except ImportError:
            webview = None

        if webview is not None:
            window = webview.create_window(
                f"{config.APP_NAME} — CrossPCAI",
                url,
                width=1360, height=880, min_size=(980, 640),
                background_color="#050810",
                text_select=True,
            )
            try:
                # Blocks until the window closes. If a tray icon is running,
                # closing the window leaves the agents up, as customers expect.
                webview.start(debug=False)
            except Exception as e:
                print(f"[jarvas] native window unavailable ({e}); using browser",
                      file=sys.stderr)
            else:
                if tray is None:
                    quit_app()
                    return 0
                print(f"[jarvas] window closed; still running in the tray - {url}")
                _idle(stopping)
                return 0
            del window

    # Browser fallback.
    print(f"[jarvas] open {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    _idle(stopping)
    return 0


def _idle(stopping: threading.Event) -> None:
    try:
        while not stopping.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    time.sleep(0.2)
