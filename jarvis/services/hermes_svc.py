#!/usr/bin/env python3
"""
hermes_svc.py — built-in Hermes automation daemon (`jarvis --service hermes`).

Wire-compatible with the existing CrossPCAI hermes_agent.py so JARVIS can
drive either one:  POST /task   GET /tasks   GET /status

A single worker thread drains a priority queue, so tasks from nine agents
never race each other on the same machine.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import ThreadingHTTPServer
from pathlib import Path

from .. import config
from .common import JsonHandler, is_blocked

MAX_HISTORY = 100
TASK_TIMEOUT = 900

_queue: deque = deque()
_history: deque = deque(maxlen=MAX_HISTORY)
_lock = threading.Lock()
_started = time.time()
_current: dict | None = None


class Task:
    __slots__ = ("id", "description", "priority", "status", "result",
                 "created_at", "started_at", "finished_at", "agent")

    def __init__(self, description: str, priority: str = "normal"):
        self.id = uuid.uuid4().hex[:8]
        self.description = description
        self.priority = priority if priority in ("high", "normal") else "normal"
        self.status = "pending"
        self.result = None
        self.created_at = time.time()
        self.started_at = None
        self.finished_at = None
        # Tasks dispatched by JARVIS carry an "[agent-id] " prefix.
        self.agent = (description.split("]")[0][1:]
                      if description.startswith("[") and "]" in description else None)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "description": self.description, "priority": self.priority,
            "status": self.status, "result": self.result, "agent": self.agent,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _run_task(task: Task) -> None:
    """Execute a task. A leading `$` or `!` marks a shell command; anything
    else is recorded as a note for an agent/operator to pick up."""
    body = task.description
    if task.agent:
        body = body.split("]", 1)[1].strip()

    if not (body.startswith("$") or body.startswith("!")):
        task.result = {"kind": "note", "text": body}
        task.status = "done"
        return

    cmd = body[1:].strip()
    if is_blocked(cmd):
        task.status = "failed"
        task.result = {"kind": "blocked", "error": "command blocked by safety policy"}
        return

    try:
        shell = ["cmd", "/c", cmd] if sys.platform == "win32" else ["/bin/sh", "-c", cmd]
        p = subprocess.run(shell, capture_output=True, text=True, timeout=TASK_TIMEOUT,
                           cwd=str(Path.home()))
        task.result = {
            "kind": "shell",
            "code": p.returncode,
            "stdout": p.stdout[-8000:],
            "stderr": p.stderr[-4000:],
        }
        task.status = "done" if p.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        task.status = "failed"
        task.result = {"kind": "shell", "error": f"timed out after {TASK_TIMEOUT}s"}
    except OSError as e:
        task.status = "failed"
        task.result = {"kind": "shell", "error": str(e)}


def _worker() -> None:
    global _current
    while True:
        task = None
        with _lock:
            if _queue:
                task = _queue.popleft()
                _current = task.to_dict()
        if task is None:
            _current = None
            time.sleep(0.4)
            continue
        task.status = "running"
        task.started_at = time.time()
        _run_task(task)
        task.finished_at = time.time()
        with _lock:
            _current = None


class Handler(JsonHandler):
    def do_POST(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if self.path.rstrip("/") != "/task":
            return self._json(404, {"error": "not found"})
        body = self._body()
        desc = (body.get("description") or "").strip()
        if not desc:
            return self._json(400, {"error": "description required"})
        task = Task(desc, body.get("priority", "normal"))
        with _lock:
            if task.priority == "high":
                _queue.appendleft(task)
            else:
                _queue.append(task)
            _history.appendleft(task)
        return self._json(200, {"ok": True, "id": task.id, "queued": len(_queue)})

    def do_GET(self):
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        p = self.path.rstrip("/") or "/"
        if p == "/tasks":
            with _lock:
                return self._json(200, {
                    "queue": [t.to_dict() for t in _queue],
                    "history": [t.to_dict() for t in _history],
                    "current": _current,
                })
        if p in ("/status", "/"):
            with _lock:
                qn, hn = len(_queue), len(_history)
            return self._json(200, {
                "ok": True, "service": "hermes", "version": config.VERSION,
                "uptime": time.time() - _started, "queued": qn, "history": hn,
                "current": _current, "port": self.server.server_address[1],
            })
        return self._json(404, {"error": "not found"})


def serve(port: int = 5562, bind: str = "127.0.0.1") -> None:
    Handler.token = config.load_token()
    threading.Thread(target=_worker, daemon=True, name="hermes-worker").start()
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    print(f"[hermes] listening on {bind}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
