#!/usr/bin/env python3
"""
nodes.py — one interface, every machine.

A JARVIS install on Ubuntu, Windows, a TrueNAS jail or a CrossPC AI OS box is
the same binary in a different role. Each install is a "node". Pair them once
and the UI's node switcher drives any of them from any of them - the Windows
laptop can queue a Hermes task on the Ubuntu box, read its sandbox and watch
its services, without a second app.

Nodes are reached over plain HTTP with the shared bearer token. The local node
always exists and cannot be removed.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
import time
import uuid

from . import config, store
from .connectors import base

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'workstation',
    platform    TEXT NOT NULL DEFAULT '',
    token       TEXT NOT NULL DEFAULT '',
    is_local    INTEGER NOT NULL DEFAULT 0,
    last_seen   REAL,
    last_error  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
"""

ROLES = ("workstation", "server", "appliance")


def _init(cfg: dict) -> None:
    c = store.connect()
    c.executescript(SCHEMA)
    c.commit()
    if not c.execute("SELECT 1 FROM nodes WHERE is_local=1").fetchone():
        info = config.host_info()
        port = cfg.get("ui_port", 5580)
        c.execute(
            "INSERT INTO nodes (id,name,url,role,platform,token,is_local,last_seen,created_at) "
            "VALUES (?,?,?,?,?,'',1,?,?)",
            ("local", info["hostname"], f"http://127.0.0.1:{port}",
             "server" if cfg.get("bind") == "0.0.0.0" else "workstation",
             info["platform"], time.time(), time.time()),
        )
        c.commit()


def _row(r) -> dict:
    d = dict(r)
    d["is_local"] = bool(d["is_local"])
    d["has_token"] = bool(d.pop("token", ""))
    return d


def list_nodes() -> list[dict]:
    rows = store.connect().execute(
        "SELECT * FROM nodes ORDER BY is_local DESC, name"
    ).fetchall()
    return [_row(r) for r in rows]


def get(nid: str, with_token: bool = False) -> dict | None:
    r = store.connect().execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    if not r:
        return None
    return dict(r) if with_token else _row(r)


def add(name: str, url: str, token: str = "", role: str = "workstation") -> dict:
    """Pair a remote node. Verifies it answers before saving, so a typo fails
    loudly here instead of silently later."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return {"ok": False, "error": "url required"}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if ":" not in url.split("//", 1)[1]:
        url += ":5580"

    probe = ping_url(url, token)
    if not probe.get("ok"):
        return {"ok": False, "error": f"no JARVIS node answered at {url}: "
                                      f"{probe.get('error', 'unreachable')}"}

    nid = uuid.uuid4().hex[:10]
    c = store.connect()
    c.execute(
        "INSERT INTO nodes (id,name,url,role,platform,token,is_local,last_seen,created_at) "
        "VALUES (?,?,?,?,?,?,0,?,?)",
        (nid, name or probe.get("hostname") or url, url,
         role if role in ROLES else "workstation",
         probe.get("platform", ""), token, time.time(), time.time()),
    )
    c.commit()
    store.log_event("nodes", f"Paired node: {name or url}", meta={"id": nid})
    return {"ok": True, **(get(nid) or {})}


def remove(nid: str) -> bool:
    if nid == "local":
        return False  # the local node is not removable
    c = store.connect()
    cur = c.execute("DELETE FROM nodes WHERE id=? AND is_local=0", (nid,))
    c.commit()
    return cur.rowcount > 0


def rename(nid: str, name: str) -> dict | None:
    c = store.connect()
    c.execute("UPDATE nodes SET name=? WHERE id=?", (name, nid))
    c.commit()
    return get(nid)


# -- reachability -------------------------------------------------------------

def ping_url(url: str, token: str = "", timeout: float = 4.0) -> dict:
    try:
        d = base.get(f"{url.rstrip('/')}/api/node/hello",
                     token=token or None, timeout=timeout)
    except base.HttpError as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(d, dict) or d.get("app") != "jarvis":
        return {"ok": False, "error": "responded, but not a JARVIS node"}
    return {"ok": True, **d}


def ping(nid: str) -> dict:
    n = get(nid, with_token=True)
    if not n:
        return {"ok": False, "error": "node not found"}
    res = ping_url(n["url"], n.get("token", ""))
    c = store.connect()
    if res.get("ok"):
        c.execute("UPDATE nodes SET last_seen=?, last_error='' WHERE id=?",
                  (time.time(), nid))
    else:
        c.execute("UPDATE nodes SET last_error=? WHERE id=?",
                  (res.get("error", "")[:300], nid))
    c.commit()
    return res


def ping_all() -> list[dict]:
    out = []
    for n in list_nodes():
        res = ping(n["id"]) if not n["is_local"] else {"ok": True, "local": True}
        out.append({**n, "online": bool(res.get("ok")),
                    "error": res.get("error", "")})
    return out


def proxy(nid: str, method: str, path: str, body: dict | None = None,
          timeout: float = 30.0) -> dict:
    """Forward an API call to a paired node. This is what lets one window
    operate a different machine."""
    n = get(nid, with_token=True)
    if not n:
        return {"ok": False, "error": "node not found"}
    if n["is_local"]:
        return {"ok": False, "error": "local node is handled in-process"}
    url = n["url"].rstrip("/") + "/" + path.lstrip("/")
    try:
        data = base.request(method, url, token=n.get("token") or None,
                            json_body=body, timeout=timeout)
        return {"ok": True, "data": data}
    except base.HttpError as e:
        return {"ok": False, "error": str(e), "status": e.status}


# -- discovery ----------------------------------------------------------------

def _local_subnet() -> str | None:
    """Best guess at this machine's /24 - used only for an explicit LAN scan."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packet is sent; this just picks a route
        ip = s.getsockname()[0]
        s.close()
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except OSError:
        return None


def discover(port: int = 5580, timeout: float = 0.35) -> list[dict]:
    """Scan the local /24 for other JARVIS installs. Runs only when the
    customer presses Scan - never automatically in the background."""
    net = _local_subnet()
    if not net:
        return []
    known = {n["url"] for n in list_nodes()}
    found: list[dict] = []

    def probe(host: str):
        if not base.port_open(host, port, timeout=timeout):
            return None
        url = f"http://{host}:{port}"
        if url in known:
            return None
        r = ping_url(url, timeout=2.0)
        return {"url": url, "hostname": r.get("hostname", host),
                "platform": r.get("platform", ""), "role": r.get("role", "")} \
            if r.get("ok") else None

    hosts = [str(h) for h in ipaddress.ip_network(net).hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        for res in pool.map(probe, hosts):
            if res:
                found.append(res)
    return found
