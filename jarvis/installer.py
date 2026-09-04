#!/usr/bin/env python3
"""
installer.py — JARVIS installs itself.

The shipped product is one executable, so registering it with the desktop is a
role of that executable rather than a separate script the customer has to find:

    jarvis --install            put the icon in place and start at login
    jarvis --install --no-autostart
    jarvis --uninstall          remove the icon and the autostart entry
    jarvis --autostart on|off   change just the login behaviour

install.sh and install.ps1 stay for admins doing a fleet rollout; they copy the
files into place and then call this. Everything here is per-user and needs no
administrator rights, which is what makes a customer double-click install work.

Nothing here touches the customer's data: uninstall removes shortcuts and the
login entry, never ~/.crosspcai.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

APP_NAME = "JARVIS"
LAUNCH_ID = "ai.crosspc.jarvis"


# -- what to launch ------------------------------------------------------------

def launch_target() -> tuple[str, list[str]]:
    """The command that starts JARVIS, as (program, args).

    Frozen: the binary itself. From source: the interpreter with -m jarvis,
    preferring pythonw on Windows so no console window flashes up.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, []

    exe = sys.executable
    if sys.platform == "win32":
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.exists():
            exe = str(pythonw)
    return exe, ["-m", "jarvis"]


def _quoted_command() -> str:
    prog, args = launch_target()
    parts = [f'"{prog}"'] + args
    return " ".join(parts)


def _working_dir() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    # Source runs need the project root on the path, not the package dir.
    return str(Path(__file__).resolve().parent.parent)


def icon_path() -> Path | None:
    base = Path(__file__).parent / "assets"
    for name in ("icon.ico", "icon.png", "icon.svg"):
        p = base / name
        if p.exists():
            return p
    return None


# -- Windows -------------------------------------------------------------------

_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _win_shortcut(path: Path) -> bool:
    """Create a .lnk via PowerShell — no pywin32 dependency in the frozen app."""
    prog, args = launch_target()
    icon = icon_path()
    ps = (
        "$s = New-Object -ComObject WScript.Shell; "
        f"$l = $s.CreateShortcut('{path}'); "
        f"$l.TargetPath = '{prog}'; "
        f"$l.Arguments = '{' '.join(args)}'; "
        f"$l.WorkingDirectory = '{_working_dir()}'; "
        f"$l.Description = 'JARVIS - CrossPCAI control centre'; "
        + (f"$l.IconLocation = '{icon}'; " if icon else "")
        + "$l.Save()"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=True, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return path.exists()
    except (subprocess.SubprocessError, OSError):
        return False


def _win_install(autostart: bool) -> dict:
    made = []
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    desktop = Path(os.path.expanduser("~/Desktop"))
    for target in (start_menu / f"{APP_NAME}.lnk", desktop / f"{APP_NAME}.lnk"):
        if _win_shortcut(target):
            made.append(str(target))
    if autostart:
        _win_autostart(True)
    return {"ok": bool(made), "shortcuts": made}


def _win_autostart(enable: bool) -> dict:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _quoted_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return {"ok": True, "autostart": enable}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _win_autostart_on() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def _win_uninstall() -> dict:
    removed = []
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    desktop = Path(os.path.expanduser("~/Desktop"))
    for target in (start_menu / f"{APP_NAME}.lnk", desktop / f"{APP_NAME}.lnk"):
        if target.exists():
            try:
                target.unlink()
                removed.append(str(target))
            except OSError:
                pass
    _win_autostart(False)
    return {"ok": True, "removed": removed}


# -- Linux ---------------------------------------------------------------------

def _linux_desktop_entry(exec_cmd: str, autostart: bool = False) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "GenericName=CrossPCAI Control Centre\n"
        "Comment=Chat, agents, sandbox and Slack in one app\n"
        f"Exec={exec_cmd}\n"
        "Icon=jarvis\n"
        "Terminal=false\n"
        "Categories=Utility;Development;Network;\n"
        "Keywords=ai;agents;automation;crosspcai;jarvis;\n"
        "StartupNotify=true\n"
        "StartupWMClass=JARVIS\n"
        "SingleMainWindow=true\n"
        + ("X-GNOME-Autostart-enabled=true\n" if autostart else "")
    )


def _linux_paths() -> dict[str, Path]:
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return {
        "desktop": data / "applications" / "jarvis.desktop",
        "autostart": cfg / "autostart" / "jarvis.desktop",
        "icon": data / "icons/hicolor/512x512/apps/jarvis.png",
    }


def _linux_install(autostart: bool) -> dict:
    p = _linux_paths()
    made = []

    src_icon = Path(__file__).parent / "assets" / "icon.png"
    if src_icon.exists():
        p["icon"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src_icon, p["icon"])
        made.append(str(p["icon"]))

    p["desktop"].parent.mkdir(parents=True, exist_ok=True)
    p["desktop"].write_text(_linux_desktop_entry(_quoted_command()), encoding="utf-8")
    p["desktop"].chmod(0o755)
    made.append(str(p["desktop"]))

    for tool, arg in (("update-desktop-database", str(p["desktop"].parent)),
                      ("gtk-update-icon-cache", str(p["icon"].parents[3]))):
        if shutil.which(tool):
            subprocess.run([tool, arg], capture_output=True, timeout=30, check=False)

    if autostart:
        _linux_autostart(True)
    return {"ok": True, "installed": made}


def _linux_autostart(enable: bool) -> dict:
    p = _linux_paths()
    if enable:
        p["autostart"].parent.mkdir(parents=True, exist_ok=True)
        p["autostart"].write_text(
            _linux_desktop_entry(_quoted_command(), autostart=True), encoding="utf-8")
        p["autostart"].chmod(0o755)
    else:
        try:
            p["autostart"].unlink(missing_ok=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "autostart": enable}


def _linux_autostart_on() -> bool:
    return _linux_paths()["autostart"].exists()


def _linux_uninstall() -> dict:
    p = _linux_paths()
    removed = []
    for key in ("desktop", "autostart", "icon"):
        try:
            if p[key].exists():
                p[key].unlink()
                removed.append(str(p[key]))
        except OSError:
            pass
    return {"ok": True, "removed": removed}


# -- macOS ---------------------------------------------------------------------

def _mac_plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LAUNCH_ID}.plist"


def _mac_autostart(enable: bool) -> dict:
    plist = _mac_plist_path()
    if not enable:
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)],
                           capture_output=True, check=False)
            try:
                plist.unlink()
            except OSError as e:
                return {"ok": False, "error": str(e)}
        return {"ok": True, "autostart": False}

    prog, args = launch_target()
    arg_xml = "".join(f"        <string>{a}</string>\n" for a in args)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "  <dict>\n"
        f"    <key>Label</key><string>{LAUNCH_ID}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{prog}</string>\n{arg_xml}"
        "    </array>\n"
        "    <key>RunAtLoad</key><true/>\n"
        "    <key>KeepAlive</key><false/>\n"
        "  </dict>\n"
        "</plist>\n",
        encoding="utf-8")
    subprocess.run(["launchctl", "load", str(plist)], capture_output=True, check=False)
    return {"ok": True, "autostart": True}


def _mac_install(autostart: bool) -> dict:
    """On macOS the .app bundle in /Applications is the icon; the installer's
    job here is the login item."""
    if autostart:
        return _mac_autostart(True)
    return {"ok": True, "note": "Drag JARVIS.app to /Applications for the icon."}


def _mac_uninstall() -> dict:
    return _mac_autostart(False)


# -- public API ----------------------------------------------------------------

def install(autostart: bool = True) -> dict:
    if sys.platform == "win32":
        res = _win_install(autostart)
    elif sys.platform == "darwin":
        res = _mac_install(autostart)
    else:
        res = _linux_install(autostart)

    # Remember the choice so the Settings toggle shows the truth.
    cfg = config.load()
    cfg.setdefault("ui", {})["launch_on_login"] = bool(autostart)
    cfg["installed"] = True
    config.save(cfg)
    return {**res, "autostart": autostart}


def uninstall() -> dict:
    if sys.platform == "win32":
        res = _win_uninstall()
    elif sys.platform == "darwin":
        res = _mac_uninstall()
    else:
        res = _linux_uninstall()

    cfg = config.load()
    cfg.setdefault("ui", {})["launch_on_login"] = False
    cfg["installed"] = False
    config.save(cfg)
    return res


def set_autostart(enable: bool) -> dict:
    if sys.platform == "win32":
        res = _win_autostart(enable)
    elif sys.platform == "darwin":
        res = _mac_autostart(enable)
    else:
        res = _linux_autostart(enable)

    if res.get("ok"):
        cfg = config.load()
        cfg.setdefault("ui", {})["launch_on_login"] = bool(enable)
        config.save(cfg)
    return res


def autostart_enabled() -> bool:
    try:
        if sys.platform == "win32":
            return _win_autostart_on()
        if sys.platform == "darwin":
            return _mac_plist_path().exists()
        return _linux_autostart_on()
    except OSError:
        return False


def status() -> dict:
    """What the Settings pane shows — real state, not what config claims."""
    shortcuts: list[str] = []
    if sys.platform == "win32":
        start_menu = Path(os.environ.get("APPDATA", "")) / \
            "Microsoft/Windows/Start Menu/Programs" / f"{APP_NAME}.lnk"
        desktop = Path(os.path.expanduser("~/Desktop")) / f"{APP_NAME}.lnk"
        shortcuts = [str(p) for p in (start_menu, desktop) if p.exists()]
    elif sys.platform == "darwin":
        app = Path("/Applications/JARVIS.app")
        shortcuts = [str(app)] if app.exists() else []
    else:
        d = _linux_paths()["desktop"]
        shortcuts = [str(d)] if d.exists() else []

    prog, args = launch_target()
    return {
        "platform": sys.platform,
        "frozen": bool(getattr(sys, "frozen", False)),
        "installed": bool(shortcuts),
        "shortcuts": shortcuts,
        "autostart": autostart_enabled(),
        "command": " ".join([prog] + args),
    }
