#!/usr/bin/env python3
"""
sandbox.py — client for the CrossPCAI sandbox agent (crosspcai_sandbox.py, :5561).

Endpoints:  GET /sandbox/files  GET /sandbox/read  GET /sandbox/status
            POST /sandbox/chat  POST /sandbox/exec  POST /sandbox/write
            DELETE /sandbox/session
"""

from __future__ import annotations

import urllib.parse

from . import base


class Sandbox:
    def __init__(self, host: str, port: int, token: str):
        self.base_url = f"http://{host}:{port}"
        self.host, self.port, self.token = host, port, token

    def alive(self) -> bool:
        return base.port_open(self.host, self.port)

    def status(self) -> dict:
        try:
            return {"ok": True, **(base.get(f"{self.base_url}/sandbox/status",
                                            token=self.token) or {})}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}

    def files(self) -> list:
        try:
            data = base.get(f"{self.base_url}/sandbox/files", token=self.token) or {}
        except base.HttpError:
            return []
        return data.get("files", data) if isinstance(data, dict) else data

    def read(self, name: str) -> dict:
        q = urllib.parse.urlencode({"name": name})
        try:
            return {"ok": True, **(base.get(f"{self.base_url}/sandbox/read?{q}",
                                            token=self.token) or {})}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}

    def write(self, name: str, content: str) -> dict:
        try:
            return {"ok": True, **(base.post(f"{self.base_url}/sandbox/write", token=self.token,
                                             json_body={"name": name, "content": content}) or {})}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}

    def exec(self, cmd: str, timeout: int = 60) -> dict:
        try:
            res = base.post(f"{self.base_url}/sandbox/exec", token=self.token,
                            json_body={"cmd": cmd, "timeout": timeout},
                            timeout=timeout + 10) or {}
            return {"ok": True, **res}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}

    def chat(self, message: str) -> dict:
        """Sandbox chat auto-executes bash blocks server-side and returns results."""
        try:
            res = base.post(f"{self.base_url}/sandbox/chat", token=self.token,
                            json_body={"message": message}, timeout=180) or {}
            return {"ok": True, **res}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}

    def reset(self) -> dict:
        try:
            base.request("DELETE", f"{self.base_url}/sandbox/session", token=self.token)
            return {"ok": True}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}
