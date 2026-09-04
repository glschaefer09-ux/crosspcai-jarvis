#!/usr/bin/env python3
"""
sandbox_svc.py — built-in sandbox agent (`jarvis --service sandbox`).

Wire-compatible with crosspcai_sandbox.py:
  GET  /sandbox/files  /sandbox/read?name=  /sandbox/status
  POST /sandbox/exec   /sandbox/write
  DELETE /sandbox/session

Every command runs with the working directory pinned to ~/.crosspcai/sandbox
and paths are resolved against it, so a customer's assistant can experiment
without reaching into the rest of their disk.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from .. import config
from .common import JsonHandler, is_blocked

SANDBOX_DIR = config.CONFIG_DIR / "sandbox"
EXEC_TIMEOUT = 60
MAX_READ = 512 * 1024
_started = time.time()
_history: list[dict] = []


def _safe_path(name: str) -> Path | None:
    """Resolve `name` inside SANDBOX_DIR, refusing traversal and absolutes."""
    if not name or name.startswith(("/", "\\")) or ":" in name:
        return None
    target = (SANDBOX_DIR / name).resolve()
    try:
        target.relative_to(SANDBOX_DIR.resolve())
    except ValueError:
        return None
    return target


def sandbox_exec(cmd: str, timeout: int = EXEC_TIMEOUT) -> dict:
    if is_blocked(cmd):
        return {"ok": False, "code": -1, "stdout": "",
                "stderr": "command blocked by safety policy", "blocked": True}
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    shell = ["cmd", "/c", cmd] if sys.platform == "win32" else ["/bin/sh", "-c", cmd]
    env = dict(os.environ)
    env["HOME"] = str(SANDBOX_DIR) if sys.platform != "win32" else env.get("HOME", "")
    started = time.time()
    try:
        p = subprocess.run(shell, capture_output=True, text=True,
                           timeout=min(timeout, 300), cwd=str(SANDBOX_DIR), env=env)
        out = {"ok": p.returncode == 0, "code": p.returncode,
               "stdout": p.stdout[-16000:], "stderr": p.stderr[-8000:]}
    except subprocess.TimeoutExpired:
        out = {"ok": False, "code": -1, "stdout": "",
               "stderr": f"timed out after {timeout}s"}
    except OSError as e:
        out = {"ok": False, "code": -1, "stdout": "", "stderr": str(e)}
    out["duration"] = round(time.time() - started, 3)
    _history.append({"cmd": cmd, "code": out["code"], "at": time.time()})
    del _history[:-200]
    return out


def list_files() -> list[dict]:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(SANDBOX_DIR.rglob("*")):
        if p.is_dir():
            continue
        try:
            rel = p.relative_to(SANDBOX_DIR).as_posix()
            st = p.stat()
        except (OSError, ValueError):
            continue
        files.append({"name": rel, "size": st.st_size, "modified": st.st_mtime})
        if len(files) >= 500:
            break
    return files


class Handler(JsonHandler):
    def do_GET(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path.rstrip("/")

        if p == "/sandbox/files":
            return self._json(200, {"ok": True, "files": list_files(),
                                    "dir": str(SANDBOX_DIR)})
        if p == "/sandbox/read":
            name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0]
            target = _safe_path(name)
            if not target:
                return self._json(400, {"error": "invalid path"})
            if not target.exists():
                return self._json(404, {"error": "not found"})
            try:
                if target.stat().st_size > MAX_READ:
                    return self._json(413, {"error": "file too large to preview"})
                return self._json(200, {"ok": True, "name": name,
                                        "content": target.read_text("utf-8", "replace")})
            except OSError as e:
                return self._json(500, {"error": str(e)})
        if p in ("/sandbox/status", "/status", ""):
            return self._json(200, {
                "ok": True, "service": "sandbox", "version": config.VERSION,
                "uptime": time.time() - _started, "dir": str(SANDBOX_DIR),
                "files": len(list_files()), "commands_run": len(_history),
                "port": self.server.server_address[1],
            })
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        p = self.path.rstrip("/")
        body = self._body()

        if p == "/sandbox/exec":
            cmd = (body.get("cmd") or "").strip()
            if not cmd:
                return self._json(400, {"error": "cmd required"})
            try:
                timeout = int(body.get("timeout", EXEC_TIMEOUT))
            except (TypeError, ValueError):
                timeout = EXEC_TIMEOUT
            return self._json(200, sandbox_exec(cmd, timeout))

        if p == "/sandbox/write":
            target = _safe_path(body.get("name", ""))
            if not target:
                return self._json(400, {"error": "invalid filename"})
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body.get("content", ""), encoding="utf-8")
            except OSError as e:
                return self._json(500, {"error": str(e)})
            return self._json(200, {"ok": True, "path": str(target)})

        return self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if self.path.rstrip("/") == "/sandbox/session":
            _history.clear()
            return self._json(200, {"ok": True, "cleared": True})
        return self._json(404, {"error": "not found"})


def serve(port: int = 5561, bind: str = "127.0.0.1") -> None:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    Handler.token = config.load_token()
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    print(f"[sandbox] listening on {bind}:{port} (dir={SANDBOX_DIR})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
