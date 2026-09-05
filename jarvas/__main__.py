#!/usr/bin/env python3
"""
__main__.py — the single entry point for every role JARVAS plays.

    jarvas                     desktop app: window + tray icon (the app icon)
    jarvas --server            headless: serves the same UI to a browser
    jarvas --service hermes    background daemon, launched by the supervisor
    jarvas --status            one-shot health check for scripts and CI
    jarvas --reset-setup       run the first-run wizard again

One binary, one icon. Which role it takes is a flag, not a separate download.
"""

from __future__ import annotations

import argparse
import sys

from . import config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvas",
                                description=f"{config.APP_NAME} {config.VERSION} — "
                                            "CrossPCAI control centre")
    p.add_argument("--server", action="store_true",
                   help="run headless and serve the UI over the network")
    p.add_argument("--service", metavar="ID",
                   help="run one background service (hermes, sandbox)")
    p.add_argument("--port", type=int, help="port to listen on")
    p.add_argument("--bind", help="address to bind (default 127.0.0.1; "
                                  "0.0.0.0 with --server)")
    p.add_argument("--no-supervise", action="store_true",
                   help="do not start or manage background services")
    p.add_argument("--browser", action="store_true",
                   help="open in the default browser instead of a native window")
    p.add_argument("--status", action="store_true", help="print health and exit")
    p.add_argument("--reset-setup", action="store_true",
                   help="show the first-run wizard again")

    # Self-installation: the binary registers itself with the desktop, so a
    # customer never has to find and run a separate script.
    p.add_argument("--install", action="store_true",
                   help="add the JARVAS icon and start it at login")
    p.add_argument("--no-autostart", action="store_true",
                   help="with --install: do not start JARVAS at login")
    p.add_argument("--uninstall", action="store_true",
                   help="remove the icon and the login entry (keeps your data)")
    p.add_argument("--autostart", choices=("on", "off"),
                   help="turn start-at-login on or off")

    p.add_argument("--version", action="version",
                   version=f"{config.APP_NAME} {config.VERSION}")
    return p


def run_installer(args) -> int:
    """--install / --uninstall / --autostart, before any server starts."""
    from . import installer

    if args.uninstall:
        res = installer.uninstall()
        print("Removed:" if res.get("removed") else "Nothing to remove.")
        for p in res.get("removed", []):
            print(f"  {p}")
        print("\nYour data in ~/.crosspcai was left alone.")
        return 0 if res.get("ok") else 1

    if args.autostart:
        res = installer.set_autostart(args.autostart == "on")
        if not res.get("ok"):
            print(f"Could not change autostart: {res.get('error')}", file=sys.stderr)
            return 1
        print(f"Start at login: {'on' if args.autostart == 'on' else 'off'}")
        return 0

    res = installer.install(autostart=not args.no_autostart)
    for p in res.get("shortcuts", []) + res.get("installed", []):
        print(f"  added {p}")
    if res.get("note"):
        print(f"  {res['note']}")
    print(f"\n{config.APP_NAME} is installed."
          f"\n  Start at login: {'yes' if res.get('autostart') else 'no'}"
          f"\n  Launch it from your applications menu, or run: jarvas")
    return 0 if res.get("ok") else 1


def run_service(sid: str, port: int | None, bind: str) -> int:
    """Background role. Kept import-light so a daemon does not load the UI."""
    if sid == "hermes":
        from .services.hermes_svc import serve
        serve(port or 5562, bind)
        return 0
    if sid == "sandbox":
        from .services.sandbox_svc import serve
        serve(port or 5561, bind)
        return 0
    print(f"unknown service: {sid}", file=sys.stderr)
    return 2


def print_status() -> int:
    from .server import App
    app = App()
    st = app.supervisor.status()
    lic = None
    try:
        from . import license
        lic = license.status()
    except Exception:
        pass
    print(f"{config.APP_NAME} {config.VERSION} on {config.host_info()['hostname']}")
    if lic:
        print(f"  licence   {lic.get('label', lic.get('tier'))}"
              + (f" ({lic['days_left']} days left)" if lic.get("trial") else ""))
    print(f"  setup     {'complete' if app.configured else 'not run yet'}")
    print(f"  model     {app.chat.describe()['model']} "
          f"({'ready' if app.chat.ready() else 'unreachable'})")
    for s in st:
        print(f"  {s['label']:<9} {s['state']}"
              + (f" (pid {s['pid']})" if s.get("pid") else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # Background service role: no config mutation, no UI, no supervisor.
    if args.service:
        return run_service(args.service, args.port, args.bind or "127.0.0.1")

    if args.install or args.uninstall or args.autostart:
        return run_installer(args)

    if args.status:
        return print_status()

    cfg = config.load()
    if args.reset_setup:
        cfg["setup_complete"] = False
        config.save(cfg)
    if args.port:
        cfg["ui_port"] = args.port
    if args.no_supervise:
        cfg["supervise"] = False
    if args.server:
        cfg["bind"] = args.bind or "0.0.0.0"
    elif args.bind:
        cfg["bind"] = args.bind

    from .server import App, already_running, serve

    # Double-clicking the icon a second time should show the running app, not
    # start a rival copy that fights it for the port and the database.
    port = cfg.get("ui_port", 5580)
    if already_running(port):
        url = f"http://127.0.0.1:{port}"
        print(f"[jarvas] already running — opening {url}")
        if not args.server:
            import webbrowser
            webbrowser.open(url)
        return 0

    app = App(cfg)
    app.boot()

    if args.server:
        # Headless: TrueNAS, Docker, CrossPC AI OS, any box without a display.
        serve(app)
        return 0

    from .desktop import launch
    return launch(app, prefer_browser=args.browser)


if __name__ == "__main__":
    sys.exit(main())
