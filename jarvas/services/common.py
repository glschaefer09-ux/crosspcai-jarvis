#!/usr/bin/env python3
"""Shared HTTP handler plumbing for the built-in services."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler

# Commands refused outright. A customer's assistant must not be one typo away
# from wiping the machine it runs on.
BLOCKED = re.compile(
    r"(rm\s+-rf\s+/(?:\s|$)|mkfs(\.\w+)?\s|dd\s+if=.*of=/dev/|"
    r">\s*/dev/[sh]d[a-z]|shutdown\b|reboot\b|poweroff\b|halt\b|init\s+0|"
    r":\(\)\s*\{.*\};:|chmod\s+-R\s+777\s+/(?:\s|$)|"
    r"(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh|"
    r"format\s+[a-z]:|del\s+/[sf]\s+/q\s+[a-z]:)",
    re.I,
)


def is_blocked(cmd: str) -> bool:
    return bool(BLOCKED.search(cmd or ""))


class JsonHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler with JSON helpers and bearer auth."""

    server_version = "JARVAS"
    token = ""  # set by the service module before serve_forever

    def log_message(self, fmt, *args):  # quiet by default; logs go to the file
        pass

    # ── helpers ──────────────────────────────────────────────────────────────

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, status: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, ValueError):
            return {}

    def _authed(self) -> bool:
        if not self.token:
            return True  # unset token = local dev mode
        got = self.headers.get("Authorization", "")
        return got == f"Bearer {self.token}"

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()
