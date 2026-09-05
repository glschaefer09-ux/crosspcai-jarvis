#!/usr/bin/env python3
"""
opencode.py — drive OpenCode, the coding agent installed on the machine.

This is what turns JARVIS from "chat that can run a shell command" into "hand a
real job to a coding agent and get the work back". JARVIS keeps the queue, the
schedule, the reports and the fleet; OpenCode does the actual editing.

Two hard-won facts about the CLI shape this module, both established by testing
against a real install rather than from the docs:

1. `opencode run` REQUIRES a pseudo-terminal. Run it with a plain pipe and it
   hangs forever producing no output and no error - it never even reaches the
   model. So we allocate a pty and read from it.
2. Its HTTP server accepts a prompt and returns an ack, but nothing executes
   until a client drives the session. `POST /prompt` alone leaves the session
   at zero tokens forever. The server is therefore used for discovery and
   session listing, never as the way to get work done.

Safety: `--auto` makes OpenCode approve its own permission requests, including
destructive ones. It is off unless the customer turns it on per connector, and
the UI says plainly what it does. Everything else runs with OpenCode's own
prompts intact.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import base

# Strip the CLI's colour codes and cursor moves before anything sees the text.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")

# The CLI prints a banner line like "> build - model-name" before the answer.
_BANNER = re.compile(r"^\s*>\s+\w+\s+[·|-]\s+\S+\s*$", re.M)

DEFAULT_TIMEOUT = 900


class OpenCode:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.binary = cfg.get("binary") or shutil.which("opencode") or "opencode"
        self.model = cfg.get("model", "")
        self.agent = cfg.get("agent", "")
        self.directory = cfg.get("directory", str(Path.home()))
        self.server_url = cfg.get("server_url", "http://127.0.0.1:4096")
        # Off by design: --auto lets the coding agent approve its own shell
        # commands and file writes with nobody watching.
        self.auto_approve = bool(cfg.get("auto_approve", False))
        self.timeout = int(cfg.get("timeout", DEFAULT_TIMEOUT))

    # -- discovery ------------------------------------------------------------

    @property
    def installed(self) -> bool:
        return bool(shutil.which(self.binary) or Path(self.binary).exists())

    @property
    def pty_supported(self) -> bool:
        # Windows has no pty module; a Windows JARVIS reaches OpenCode by
        # proxying to a paired Linux node instead (see nodes.py).
        return os.name == "posix"

    def version(self) -> str:
        if not self.installed:
            return ""
        try:
            p = subprocess.run([self.binary, "--version"], capture_output=True,
                               text=True, timeout=30)
            return _clean(p.stdout).strip().splitlines()[-1] if p.stdout else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def models(self) -> list[str]:
        if not self.installed:
            return []
        try:
            p = subprocess.run([self.binary, "models"], capture_output=True,
                               text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return []
        return [ln.strip() for ln in _clean(p.stdout).splitlines()
                if "/" in ln and not ln.startswith(" ")]

    def server_alive(self) -> bool:
        try:
            host, _, port = self.server_url.split("://", 1)[1].partition(":")
            return base.port_open(host, int(port or 4096), timeout=0.5)
        except (ValueError, IndexError):
            return False

    def sessions(self) -> list[dict]:
        """Sessions from a running `opencode serve`, for the UI's history list."""
        if not self.server_alive():
            return []
        try:
            d = base.get(f"{self.server_url}/api/session", timeout=8)
        except base.HttpError:
            return []
        rows = d.get("data", d) if isinstance(d, dict) else d
        if not isinstance(rows, list):
            return []
        return [{"id": s.get("id"), "title": s.get("title", ""),
                 "cost": s.get("cost", 0), "directory": (s.get("location") or {}).get("directory", ""),
                 "created": (s.get("time") or {}).get("created")}
                for s in rows if isinstance(s, dict)]

    def status(self) -> dict:
        return {
            "installed": self.installed,
            "binary": self.binary,
            "version": self.version(),
            "pty": self.pty_supported,
            "server": self.server_alive(),
            "server_url": self.server_url,
            "model": self.model or "(opencode default)",
            "directory": self.directory,
            "auto_approve": self.auto_approve,
        }

    # -- running --------------------------------------------------------------

    def run(self, prompt: str, *, directory: str = "", model: str = "",
            session: str = "", agent: str = "", timeout: int | None = None) -> dict:
        """Hand a task to OpenCode and return what it did.

        Blocking: OpenCode edits files and runs commands, so a caller wants the
        finished result, not a handle. Callers that must not block should
        dispatch this through the Hermes queue.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt required"}
        if not self.installed:
            return {"ok": False, "error": "OpenCode is not installed on this machine.",
                    "not_installed": True}
        if not self.pty_supported:
            return {"ok": False,
                    "error": "OpenCode needs a pseudo-terminal, which Windows does "
                             "not provide here. Pair a Linux machine and run it there.",
                    "needs_posix": True}

        cwd = directory or self.directory
        if not Path(cwd).is_dir():
            return {"ok": False, "error": f"directory does not exist: {cwd}"}

        cmd = [self.binary, "run"]
        if model or self.model:
            cmd += ["-m", model or self.model]
        if agent or self.agent:
            cmd += ["--agent", agent or self.agent]
        if session:
            cmd += ["--session", session]
        if self.auto_approve:
            cmd += ["--auto"]
        cmd += ["--dir", cwd, prompt]

        started = time.time()
        out, code, timed_out = _run_in_pty(cmd, cwd, timeout or self.timeout)
        text = _strip_banner(_clean(out)).strip()

        result = {
            "ok": code == 0 and not timed_out,
            "output": text[-20000:],
            "exit_code": code,
            "duration": round(time.time() - started, 1),
            "directory": cwd,
            "model": model or self.model or "(default)",
        }
        if timed_out:
            result["error"] = f"OpenCode did not finish within {timeout or self.timeout}s."
        elif code != 0:
            result["error"] = text[-600:] or f"OpenCode exited with code {code}"

        # A credential fault reads like a code fault unless it is named.
        low = text.lower()
        if "not scoped to a workspace" in low:
            result["error"] = ("OpenCode's Anthropic key is org-scoped and needs a "
                               "workspace id. Fix it with `opencode providers` or pick "
                               "another model.")
            result["credential_problem"] = True
        elif "no such model" in low or "unknown model" in low:
            result["credential_problem"] = True
        return result


# -- helpers ------------------------------------------------------------------

def _clean(text: str) -> str:
    return _ANSI.sub("", text or "")


def _strip_banner(text: str) -> str:
    return _BANNER.sub("", text or "")


def _run_in_pty(cmd: list[str], cwd: str, timeout: int) -> tuple[str, int, bool]:
    """Run a command attached to a pty and collect its output.

    OpenCode checks for a terminal and blocks silently without one, so a plain
    subprocess pipe is not an option here.
    """
    import pty
    import select

    master, slave = pty.openpty()
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    env["NO_COLOR"] = "1"

    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=slave,
                                stdout=slave, stderr=slave, close_fds=True,
                                start_new_session=True)
    except OSError as e:
        os.close(master)
        os.close(slave)
        return f"could not start OpenCode: {e}", 127, False

    os.close(slave)
    chunks: list[bytes] = []
    deadline = time.time() + timeout
    timed_out = False

    try:
        while True:
            if time.time() > deadline:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            ready, _, _ = select.select([master], [], [], 1.0)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break  # pty closed - the child is gone
                if not data:
                    break
                chunks.append(data)
            elif proc.poll() is not None:
                break
    finally:
        try:
            os.close(master)
        except OSError:
            pass

    if proc.poll() is None:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return (b"".join(chunks).decode("utf-8", "replace"),
            proc.returncode if proc.returncode is not None else -1,
            timed_out)
