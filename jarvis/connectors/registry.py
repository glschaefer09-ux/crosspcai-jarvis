#!/usr/bin/env python3
"""
registry.py — connector catalog, customer-built connectors, and connector requests.

Three things live here:

1. CATALOG — connectors JARVIS ships with, and whether each is configured.
2. Custom connectors — a customer can wire any HTTP/webhook API themselves
   (base URL, auth style, headers, a test call) without waiting on a release.
   Agents bind to these by id.
3. Requests — when a customer needs a connector that does not exist yet, they
   can *optionally* send a report to CrossPCAI. Nothing is sent unless they
   press the button: `request()` stores locally, `send_request()` transmits.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import uuid

from .. import needs, store, telemetry
from . import base

SCHEMA = """
CREATE TABLE IF NOT EXISTS connectors (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'http',
    base_url    TEXT NOT NULL DEFAULT '',
    auth_type   TEXT NOT NULL DEFAULT 'none',
    auth_value  TEXT NOT NULL DEFAULT '',
    headers     TEXT NOT NULL DEFAULT '{}',
    test_path   TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""

# Built-in connectors. `configured` is computed live from config, so the UI
# always shows real state rather than a stored guess.
CATALOG = [
    {"id": "hermes", "name": "Hermes", "category": "core",
     "description": "Automation daemon and task queue", "builtin": True},
    {"id": "sandbox", "name": "Sandbox", "category": "core",
     "description": "Isolated command execution and file workspace", "builtin": True},
    {"id": "chat", "name": "AI Model", "category": "core",
     "description": "Ollama, Anthropic or OpenAI chat model", "builtin": True},
    {"id": "slack", "name": "Slack", "category": "messaging",
     "description": "Read channels and post messages", "builtin": True},
    {"id": "notion", "name": "Notion", "category": "knowledge",
     "description": "Push records and reports into Notion", "builtin": True},
    {"id": "webhook", "name": "Webhook / n8n", "category": "automation",
     "description": "Fire events at any automation platform", "builtin": True},
]

MASK = "•" * 8


def _init() -> None:
    c = store.connect()
    c.executescript(SCHEMA)
    c.commit()


def _row(r) -> dict:
    d = dict(r)
    try:
        d["headers"] = json.loads(d.get("headers") or "{}")
    except json.JSONDecodeError:
        d["headers"] = {}
    d["enabled"] = bool(d["enabled"])
    # Never hand a stored secret back to the browser.
    if d.get("auth_value"):
        d["auth_value"] = MASK
        d["has_auth"] = True
    else:
        d["has_auth"] = False
    return d


# -- catalog ------------------------------------------------------------------

def catalog(cfg: dict, supervisor=None, slack=None, chat=None) -> list[dict]:
    """Built-in connectors with live configured/health state."""
    out = []
    for item in CATALOG:
        entry = dict(item)
        cid = entry["id"]
        if cid == "slack":
            entry["configured"] = bool(cfg.get("slack", {}).get("bot_token"))
            entry["healthy"] = bool(slack and slack.configured)
        elif cid == "notion":
            t = cfg.get("telemetry", {})
            entry["configured"] = bool(t.get("notion_token") or t.get("ingest_url"))
            entry["healthy"] = entry["configured"]
        elif cid == "chat":
            entry["configured"] = True
            entry["healthy"] = bool(chat and chat.ready())
        elif cid in ("hermes", "sandbox") and supervisor:
            svc = supervisor.get(cid)
            entry["configured"] = True
            entry["healthy"] = bool(svc and svc.state() in ("running", "external"))
        elif cid == "webhook":
            entry["configured"] = bool(cfg.get("telemetry", {}).get("ingest_url"))
            entry["healthy"] = entry["configured"]
        else:
            entry["configured"] = False
            entry["healthy"] = False
        out.append(entry)
    return out


# -- custom connectors --------------------------------------------------------

def create(name: str, base_url: str = "", kind: str = "http",
           auth_type: str = "none", auth_value: str = "",
           headers: dict | None = None, test_path: str = "",
           notes: str = "") -> dict:
    cid = uuid.uuid4().hex[:10]
    now = time.time()
    c = store.connect()
    c.execute(
        "INSERT INTO connectors "
        "(id,name,kind,base_url,auth_type,auth_value,headers,test_path,notes,enabled,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
        (cid, name, kind, base_url, auth_type, auth_value,
         json.dumps(headers or {}), test_path, notes, now, now),
    )
    c.commit()
    # A hand-built connector is a strong signal about a missing integration.
    telemetry.report("connector.created", {
        "name": name, "kind": kind, "auth_type": auth_type,
        "base_url_host": urllib.parse.urlparse(base_url).netloc if base_url else "",
    })
    store.log_event("connectors", f"Connector added: {name}", meta={"id": cid})
    return get(cid)


def get(cid: str, with_secret: bool = False) -> dict | None:
    r = store.connect().execute("SELECT * FROM connectors WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    if with_secret:
        d = dict(r)
        try:
            d["headers"] = json.loads(d.get("headers") or "{}")
        except json.JSONDecodeError:
            d["headers"] = {}
        return d
    return _row(r)


def list_connectors() -> list[dict]:
    rows = store.connect().execute("SELECT * FROM connectors ORDER BY name").fetchall()
    return [_row(r) for r in rows]


def update(cid: str, **fields) -> dict | None:
    allowed = {"name", "base_url", "kind", "auth_type", "auth_value",
               "headers", "test_path", "notes", "enabled"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "auth_value" and (not v or v == MASK):
            continue  # masked placeholder came back from the UI - keep the stored one
        if k == "headers":
            v = json.dumps(v or {})
        if k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get(cid)
    sets.append("updated_at=?")
    vals.extend([time.time(), cid])
    c = store.connect()
    c.execute(f"UPDATE connectors SET {','.join(sets)} WHERE id=?", vals)
    c.commit()
    return get(cid)


def delete(cid: str) -> bool:
    c = store.connect()
    cur = c.execute("DELETE FROM connectors WHERE id=?", (cid,))
    c.commit()
    return cur.rowcount > 0


def call(cid: str, path: str = "", method: str = "GET",
         body: dict | None = None, timeout: float = 20.0) -> dict:
    """Invoke a customer-defined connector. Agents reach the outside world here."""
    conn = get(cid, with_secret=True)
    if not conn:
        return {"ok": False, "error": "connector not found"}
    if not conn.get("enabled"):
        return {"ok": False, "error": "connector disabled"}

    url = conn["base_url"].rstrip("/") + ("/" + path.lstrip("/") if path else "")
    headers = dict(conn.get("headers") or {})
    token = None
    auth_type, secret = conn.get("auth_type", "none"), conn.get("auth_value", "")
    if auth_type == "bearer" and secret:
        token = secret
    elif auth_type == "header" and secret and ":" in secret:
        k, _, v = secret.partition(":")
        headers[k.strip()] = v.strip()
    elif auth_type == "query" and secret and "=" in secret:
        url += ("&" if "?" in url else "?") + secret

    try:
        data = base.request(method, url, token=token, json_body=body,
                            headers=headers, timeout=timeout)
        return {"ok": True, "data": data}
    except base.HttpError as e:
        return {"ok": False, "error": str(e), "status": e.status}


def test(cid: str) -> dict:
    conn = get(cid)
    if not conn:
        return {"ok": False, "error": "connector not found"}
    return call(cid, conn.get("test_path", ""), "GET", timeout=10)


# -- connector requests (optional send) ---------------------------------------

def request(name: str, category: str = "", reason: str = "", detail: str = "") -> dict:
    """Record that a connector is needed. Local only - nothing is transmitted.

    Delegates to the shared needs registry so connectors, tools and app
    integrations all queue through one place and one Send report action.
    """
    return needs.record("connector", name, category=category,
                        reason=reason, detail=detail, source="user")


def list_requests() -> list[dict]:
    return needs.list_needs("connector")


def send_request(rid: str) -> dict:
    """Explicit customer action: transmit one connector request to CrossPCAI."""
    return needs.send(rid)
