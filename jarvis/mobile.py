#!/usr/bin/env python3
"""
mobile.py — the phone and tablet half of "one interface, every machine".

A JARVIS install on a desktop is a node (see nodes.py). A phone is not: it
cannot host Hermes or a sandbox, and it comes and goes from the network. So
phones register here instead, as *devices* — things JARVIS watches and sends
work to, rather than things it dials into.

The direction of travel is deliberate: the phone always calls us. It registers,
then long-polls for commands. That means no inbound port on the handset, no
push-notification dependency for control, and nothing to open on the customer's
router. A device that stops calling simply goes stale.

Pairing reuses the shared bearer token — a device that can present the token is
already trusted to drive the API, so a separate device secret would be theatre.
What we do add is an explicit `approved` flag, so a customer can see a new
handset appear and revoke it without rotating the token for every machine.
"""

from __future__ import annotations

import json
import time
import uuid

from . import store, telemetry

# A handset is considered present if it has called within this window. Two
# missed heartbeats at the default 60s interval, with slack for a slow network.
STALE_AFTER = 150.0

PLATFORMS = ("android", "ios")

# Commands the fleet agents (and the customer) may send to a handset. Kept as an
# allowlist rather than free-form so a compromised or confused agent cannot
# invent an instruction the app was never built to refuse.
COMMANDS = {
    "ping":          "Ask the device to check in immediately",
    "refresh":       "Re-pull config and clear cached state",
    "collect_logs":  "Upload the local log buffer with the next heartbeat",
    "report_needs":  "Push any locally recorded needs to this server",
    "sign_out":      "Clear the stored token and return to the pairing screen",
    "update_check":  "Ask the app to check for a newer build",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id           TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    os_version   TEXT NOT NULL DEFAULT '',
    app_version  TEXT NOT NULL DEFAULT '',
    push_token   TEXT NOT NULL DEFAULT '',
    battery      INTEGER,
    network      TEXT NOT NULL DEFAULT '',
    approved     INTEGER NOT NULL DEFAULT 1,
    last_seen    REAL,
    last_error   TEXT NOT NULL DEFAULT '',
    heartbeats   INTEGER NOT NULL DEFAULT 0,
    registered_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS device_commands (
    id         TEXT PRIMARY KEY,
    device_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    args       TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'queued',
    result     TEXT NOT NULL DEFAULT '',
    issued_by  TEXT NOT NULL DEFAULT 'user',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cmds_device ON device_commands(device_id, status);
"""


def _init() -> None:
    c = store.connect()
    c.executescript(SCHEMA)
    c.commit()


def _row(r) -> dict:
    d = dict(r)
    d["approved"] = bool(d["approved"])
    d["online"] = bool(d.get("last_seen") and time.time() - d["last_seen"] < STALE_AFTER)
    d.pop("push_token", None)          # never leave the box in a listing
    return d


def _cmd_row(r) -> dict:
    d = dict(r)
    try:
        d["args"] = json.loads(d.get("args") or "{}")
    except (json.JSONDecodeError, ValueError):
        d["args"] = {}
    return d


# ── registration ──────────────────────────────────────────────────────────────

def register(platform: str, name: str, model: str = "", os_version: str = "",
             app_version: str = "", push_token: str = "",
             device_id: str = "") -> dict:
    """Called once by the app after the customer pairs it.

    The app generates and keeps its own id so a reinstall-and-restore does not
    orphan the old row; we honour whatever it sends and only mint one if it has
    none yet.
    """
    platform = (platform or "").lower()
    if platform not in PLATFORMS:
        return {"ok": False, "error": f"unknown platform: {platform}"}

    did = device_id or uuid.uuid4().hex[:12]
    now = time.time()
    existing = get(did)

    c = store.connect()
    if existing:
        c.execute(
            "UPDATE devices SET platform=?,name=?,model=?,os_version=?,"
            "app_version=?,push_token=?,last_seen=? WHERE id=?",
            (platform, name or existing["name"], model, os_version,
             app_version, push_token, now, did))
    else:
        c.execute(
            "INSERT INTO devices (id,platform,name,model,os_version,app_version,"
            "push_token,approved,last_seen,heartbeats,registered_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,0,?)",
            (did, platform, name, model, os_version, app_version,
             push_token, now, now))
    c.commit()

    if not existing:
        store.log_event("mobile", f"{platform} device paired: {name or did}",
                        meta={"device": did})
        telemetry.report("device.paired", {
            "platform": platform, "model": model,
            "os_version": os_version, "app_version": app_version,
        })
    return {"ok": True, "device": get(did), "device_id": did}


def get(did: str) -> dict | None:
    r = store.connect().execute("SELECT * FROM devices WHERE id=?", (did,)).fetchone()
    return _row(r) if r else None


def list_devices(platform: str = "") -> list[dict]:
    sql = "SELECT * FROM devices"
    args: tuple = ()
    if platform:
        sql += " WHERE platform=?"
        args = (platform,)
    sql += " ORDER BY last_seen DESC NULLS LAST, registered_at DESC"
    rows = store.connect().execute(sql, args).fetchall()
    return [_row(r) for r in rows]


def remove(did: str) -> bool:
    c = store.connect()
    c.execute("DELETE FROM device_commands WHERE device_id=?", (did,))
    cur = c.execute("DELETE FROM devices WHERE id=?", (did,))
    c.commit()
    if cur.rowcount:
        store.log_event("mobile", f"Device removed: {did}", meta={"device": did})
    return cur.rowcount > 0


def set_approved(did: str, approved: bool) -> dict | None:
    c = store.connect()
    c.execute("UPDATE devices SET approved=? WHERE id=?", (1 if approved else 0, did))
    c.commit()
    store.log_event("mobile",
                    f"Device {'approved' if approved else 'revoked'}: {did}",
                    meta={"device": did})
    return get(did)


# ── heartbeat ─────────────────────────────────────────────────────────────────

def heartbeat(did: str, battery: int | None = None, network: str = "",
              app_version: str = "", error: str = "") -> dict:
    """The device's periodic check-in. Returns any queued work in the same
    round trip, so a healthy handset costs exactly one request per interval."""
    dev = get(did)
    if not dev:
        return {"ok": False, "error": "unknown device", "reregister": True}
    if not dev["approved"]:
        return {"ok": False, "error": "device revoked", "revoked": True}

    c = store.connect()
    c.execute(
        "UPDATE devices SET last_seen=?, battery=?, network=?, heartbeats=heartbeats+1, "
        "last_error=?, app_version=COALESCE(NULLIF(?,''), app_version) WHERE id=?",
        (time.time(), battery, network, error or "", app_version, did))
    c.commit()

    return {"ok": True, "commands": take_commands(did),
            "interval": 60, "server_time": time.time()}


# ── command queue ─────────────────────────────────────────────────────────────

def enqueue(did: str, action: str, args: dict | None = None,
            issued_by: str = "user") -> dict:
    if action not in COMMANDS:
        return {"ok": False, "error": f"unknown command: {action}"}
    if not get(did):
        return {"ok": False, "error": "unknown device"}
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    c = store.connect()
    c.execute(
        "INSERT INTO device_commands (id,device_id,action,args,status,issued_by,"
        "created_at,updated_at) VALUES (?,?,?,?,'queued',?,?,?)",
        (cid, did, action, json.dumps(args or {}), issued_by, now, now))
    c.commit()
    store.log_event("mobile", f"Queued '{action}' for {did}",
                    meta={"device": did, "command": cid, "by": issued_by})
    return {"ok": True, "id": cid, "action": action}


def take_commands(did: str, limit: int = 10) -> list[dict]:
    """Hand the device its queued work and mark it sent in one step.

    Marking on hand-off rather than on completion means a device that dies
    mid-command does not get the same instruction on every reconnect. The
    trade-off — a command that is sent but never acknowledged — shows in the
    fleet view as 'sent' and is the fleet agent's problem to chase.
    """
    c = store.connect()
    rows = c.execute(
        "SELECT * FROM device_commands WHERE device_id=? AND status='queued' "
        "ORDER BY created_at LIMIT ?", (did, limit)).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    c.execute(
        f"UPDATE device_commands SET status='sent', updated_at=? "
        f"WHERE id IN ({','.join('?' * len(ids))})", (time.time(), *ids))
    c.commit()
    return [_cmd_row(r) for r in rows]


def complete(cid: str, ok: bool, result: str = "") -> dict:
    c = store.connect()
    cur = c.execute(
        "UPDATE device_commands SET status=?, result=?, updated_at=? WHERE id=?",
        ("done" if ok else "failed", (result or "")[:4000], time.time(), cid))
    c.commit()
    return {"ok": cur.rowcount > 0}


def commands_for(did: str, limit: int = 50) -> list[dict]:
    rows = store.connect().execute(
        "SELECT * FROM device_commands WHERE device_id=? ORDER BY created_at DESC "
        "LIMIT ?", (did, limit)).fetchall()
    return [_cmd_row(r) for r in rows]


# ── fleet view ────────────────────────────────────────────────────────────────

def summary() -> dict:
    """What the fleet agents read, and what the Machines pane shows as a
    header. Cheap enough to call on every status poll."""
    devices = list_devices()
    by_platform: dict[str, dict] = {}
    for p in PLATFORMS:
        rows = [d for d in devices if d["platform"] == p]
        by_platform[p] = {
            "total": len(rows),
            "online": sum(1 for d in rows if d["online"]),
            "stale": sum(1 for d in rows if not d["online"]),
            "revoked": sum(1 for d in rows if not d["approved"]),
            "errors": sum(1 for d in rows if d.get("last_error")),
            "versions": sorted({d["app_version"] for d in rows if d["app_version"]}),
        }
    pending = store.connect().execute(
        "SELECT COUNT(*) c FROM device_commands WHERE status IN ('queued','sent')"
    ).fetchone()["c"]
    return {"total": len(devices), "online": sum(1 for d in devices if d["online"]),
            "pending_commands": pending, "platforms": by_platform}
