#!/usr/bin/env python3
"""
supervisor.py — makes JARVAS a single self-contained application.

The shipped product is ONE executable. Background daemons are not separate
downloads or systemd units the customer has to wire up: the supervisor
re-launches this same binary with `--service <id>`, so `jarvas.exe`,
`/usr/bin/jarvas` and the Docker image all contain the whole stack.

Attach-don't-clobber rule: if something is already listening on a service's
port (e.g. Graham's existing crosspcai-hermes.service on the Ubuntu box),
JARVAS attaches to it and reports "external" rather than starting a rival
process on the same port.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config
from .connectors import base

LOG_DIR = config.CONFIG_DIR / "logs"

# Service id -> (label, default port, description)
MANAGED = {
    "hermes": ("Hermes", 5562, "Automation daemon and task queue"),
    "sandbox": ("Sandbox", 5561, "Isolated command execution and file workspace"),
}


def _launcher() -> list[str]:
    """Argv prefix that re-invokes this application.

    Frozen (PyInstaller): sys.executable IS jarvas.exe → ['jarvas.exe'].
    Source checkout:      ['python', '-m', 'jarvas'].
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "jarvas"]


class ManagedService:
    def __init__(self, sid: str, label: str, port: int, host: str = "127.0.0.1"):
        self.id, self.label, self.port, self.host = sid, label, port, host
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.restarts = 0
        self.last_error = ""
        self.external = False  # port owned by a process JARVAS did not start

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def port_live(self) -> bool:
        return base.port_open(self.host, self.port)

    def state(self) -> str:
        if self.running:
            return "running"
        if self.port_live:
            return "external"
        return "stopped"

    def info(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "port": self.port,
            "state": self.state(),
            "pid": self.proc.pid if self.running else None,
            "uptime": (time.time() - self.started_at) if (self.running and self.started_at) else 0,
            "restarts": self.restarts,
            "error": self.last_error,
            "managed": self.running,
        }

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> dict:
        if self.running:
            return {"ok": True, "state": "running", "note": "already running"}
        if self.port_live:
            self.external = True
            return {"ok": True, "state": "external",
                    "note": f"port {self.port} already served — attached to existing service"}

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logfile = LOG_DIR / f"{self.id}.log"
        env = dict(os.environ)
        env["JARVAS_CHILD"] = "1"
        env["CROSSPCAI_HOME"] = str(config.CONFIG_DIR)

        creation = 0
        if sys.platform == "win32":
            # Keep the console hidden — this is a GUI product.
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            fh = open(logfile, "ab", buffering=0)
            self.proc = subprocess.Popen(
                _launcher() + ["--service", self.id, "--port", str(self.port)],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                env=env, creationflags=creation,
                start_new_session=(sys.platform != "win32"),
            )
        except OSError as e:
            self.last_error = str(e)
            return {"ok": False, "error": str(e)}

        self.started_at = time.time()
        self.last_error = ""
        # Give it a moment to bind so the UI shows truth, not optimism.
        for _ in range(40):
            if self.port_live:
                break
            if self.proc.poll() is not None:
                self.last_error = f"exited with code {self.proc.returncode}"
                return {"ok": False, "error": self.last_error, "log": str(logfile)}
            time.sleep(0.1)
        return {"ok": True, "state": self.state(), "pid": self.proc.pid}

    def stop(self, timeout: float = 8.0) -> dict:
        if not self.running:
            return {"ok": True, "state": self.state(), "note": "not managed by JARVAS"}
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self.proc = None
        self.started_at = None
        return {"ok": True, "state": "stopped"}

    def restart(self) -> dict:
        self.stop()
        self.restarts += 1
        return self.start()

    def tail(self, lines: int = 200) -> str:
        f = LOG_DIR / f"{self.id}.log"
        if not f.exists():
            return ""
        try:
            data = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"[log unreadable: {e}]"
        return "\n".join(data[-lines:])


class Supervisor:
    """Owns every managed service plus the crash-watchdog thread."""

    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.services: dict[str, ManagedService] = {
            sid: ManagedService(sid, label, port, host)
            for sid, (label, port, _desc) in MANAGED.items()
        }
        self._watch: threading.Thread | None = None
        self._stop = threading.Event()
        self.autostart = True

    def get(self, sid: str) -> ManagedService | None:
        return self.services.get(sid)

    def start_all(self) -> dict:
        return {sid: svc.start() for sid, svc in self.services.items()}

    def stop_all(self) -> dict:
        self._stop.set()
        return {sid: svc.stop() for sid, svc in self.services.items()}

    def status(self) -> list[dict]:
        return [svc.info() for svc in self.services.values()]

    # ── watchdog ─────────────────────────────────────────────────────────────

    def begin_watch(self, interval: float = 10.0) -> None:
        if self._watch and self._watch.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval):
                if not self.autostart:
                    continue
                for svc in self.services.values():
                    # Only revive what JARVAS itself started; never fight an
                    # external systemd unit for the port.
                    if svc.proc is not None and not svc.running and not svc.port_live:
                        svc.restarts += 1
                        svc.start()

        self._watch = threading.Thread(target=loop, daemon=True, name="jarvas-watchdog")
        self._watch.start()
