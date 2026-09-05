#!/usr/bin/env python3
"""
base.py — tiny stdlib HTTP client shared by every connector.

Deliberately urllib-only: JARVAS ships as a frozen binary on five platforms,
so the core runtime must not depend on requests/httpx being importable.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: Any = None,
    form: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
) -> Any:
    """Perform an HTTP request and decode JSON when the server sends it."""
    data = None
    hdrs = {"Accept": "application/json", "User-Agent": "JARVAS/1.0"}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise HttpError(e.code, raw) from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise HttpError(0, str(e)) from None

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def get(url: str, **kw) -> Any:
    return request("GET", url, **kw)


def post(url: str, **kw) -> Any:
    return request("POST", url, **kw)


def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    """Cheap liveness probe — used by the status rail, called often."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False
