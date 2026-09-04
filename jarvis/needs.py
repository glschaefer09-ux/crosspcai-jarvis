#!/usr/bin/env python3
"""
needs.py — one registry for everything the customer needed and did not have.

Three kinds, one flow:
    connector    an API/service JARVIS cannot talk to yet
    tool         an action an agent wanted to take but had no tool for
    integration  a whole app the customer wants JARVIS to live inside

Every one of these is recorded LOCALLY the moment it happens - including
automatically, when an agent asks for a tool that does not exist. Nothing is
transmitted by that. Sending is always a separate, explicit act: the customer
presses "Send report", or nothing leaves the machine.

Delivered reports land in the CrossPCAI Notion workspace, where the agent
workforce turns them into built connectors and upgraded tools.
"""

from __future__ import annotations

import time
import uuid

from . import store, telemetry

KINDS = ("connector", "tool", "integration")

SCHEMA = """
CREATE TABLE IF NOT EXISTS needs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'user',
    hits        INTEGER NOT NULL DEFAULT 1,
    sent        INTEGER NOT NULL DEFAULT 0,
    sent_at     REAL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_needs_kind ON needs(kind, updated_at DESC);
"""


def _init() -> None:
    c = store.connect()
    c.executescript(SCHEMA)
    c.commit()


def _row(r) -> dict:
    d = dict(r)
    d["sent"] = bool(d["sent"])
    return d


def record(kind: str, name: str, category: str = "", reason: str = "",
           detail: str = "", source: str = "user") -> dict:
    """Log an unmet need. Local only - never transmits.

    Repeat requests for the same thing increment `hits` instead of piling up,
    so "asked for Shopify 40 times" reads as one strong signal.
    """
    if kind not in KINDS:
        kind = "tool"
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}

    now = time.time()
    c = store.connect()
    existing = c.execute(
        "SELECT * FROM needs WHERE kind=? AND lower(name)=lower(?) AND sent=0",
        (kind, name),
    ).fetchone()

    if existing:
        c.execute(
            "UPDATE needs SET hits=hits+1, updated_at=?, "
            "detail=CASE WHEN ?<>'' THEN ? ELSE detail END WHERE id=?",
            (now, detail, detail, existing["id"]),
        )
        c.commit()
        return {"ok": True, "id": existing["id"], "sent": False,
                "hits": existing["hits"] + 1, "note": "Already tracked - count updated."}

    nid = uuid.uuid4().hex[:10]
    c.execute(
        "INSERT INTO needs (id,kind,name,category,reason,detail,source,hits,sent,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,0,?,?)",
        (nid, kind, name, category, reason, detail, source, now, now),
    )
    c.commit()
    store.log_event("needs", f"{kind.capitalize()} needed: {name}",
                    meta={"id": nid, "source": source})
    return {"ok": True, "id": nid, "sent": False, "hits": 1,
            "note": "Saved locally. Use Send report to share it with CrossPCAI."}


def missing_tool(tool_name: str, agent_name: str = "", detail: str = "") -> dict:
    """Called automatically when an agent reaches for a tool that is not wired.

    Records the gap so the customer can send it with one click later; it does
    not interrupt them and it does not transmit.
    """
    return record(
        "tool", tool_name,
        category="agent-requested",
        reason=f"Requested by agent: {agent_name}" if agent_name else "Requested by an agent",
        detail=detail, source="agent",
    )


def list_needs(kind: str = "", include_sent: bool = True) -> list[dict]:
    sql = "SELECT * FROM needs"
    args: list = []
    where = []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if not include_sent:
        where.append("sent=0")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sent, hits DESC, updated_at DESC"
    return [_row(r) for r in store.connect().execute(sql, args).fetchall()]


def get(nid: str) -> dict | None:
    r = store.connect().execute("SELECT * FROM needs WHERE id=?", (nid,)).fetchone()
    return _row(r) if r else None


def delete(nid: str) -> bool:
    c = store.connect()
    cur = c.execute("DELETE FROM needs WHERE id=?", (nid,))
    c.commit()
    return cur.rowcount > 0


def pending_count() -> dict:
    rows = store.connect().execute(
        "SELECT kind, COUNT(*) n FROM needs WHERE sent=0 GROUP BY kind"
    ).fetchall()
    out = {k: 0 for k in KINDS}
    for r in rows:
        out[r["kind"]] = r["n"]
    out["total"] = sum(out[k] for k in KINDS)
    return out


def preview(nid: str) -> dict:
    """Exactly what a send would transmit - shown before the customer confirms."""
    n = get(nid)
    if not n:
        return {"ok": False, "error": "not found"}
    return {"ok": True, "payload": {
        "event": f"{n['kind']}.requested",
        "data": {
            "name": n["name"], "category": n["category"], "kind": n["kind"],
            "reason": n["reason"], "detail": n["detail"], "count": n["hits"],
            "requested": True,
        },
    }}


def send(nid: str) -> dict:
    """Explicit customer action: transmit one report to CrossPCAI."""
    n = get(nid)
    if not n:
        return {"ok": False, "error": "not found"}
    if n["sent"]:
        return {"ok": True, "sent": True, "note": "Already sent."}
    if not telemetry.enabled():
        return {"ok": False, "needs_optin": True,
                "error": "Reporting is off. Turn it on in Settings > Privacy to send."}

    telemetry.report(f"{n['kind']}.requested", {
        "name": n["name"], "category": n["category"], "kind": n["kind"],
        "reason": n["reason"], "detail": n["detail"], "count": n["hits"],
        "requested": True,
    })
    res = telemetry.flush()
    if res.get("ok"):
        c = store.connect()
        c.execute("UPDATE needs SET sent=1, sent_at=? WHERE id=?", (time.time(), nid))
        c.commit()
        store.log_event("needs", f"Report sent: {n['name']}", meta={"id": nid})
        return {"ok": True, "sent": True}
    return {"ok": False, "queued": True,
            "error": res.get("reason", "delivery failed - it stays queued and retries")}


def send_all(kind: str = "") -> dict:
    """Send every pending report of a kind. Still one deliberate customer action."""
    if not telemetry.enabled():
        return {"ok": False, "needs_optin": True,
                "error": "Reporting is off. Turn it on in Settings > Privacy to send."}
    pending = [n for n in list_needs(kind, include_sent=False)]
    if not pending:
        return {"ok": True, "sent": 0, "note": "Nothing pending."}
    for n in pending:
        telemetry.report(f"{n['kind']}.requested", {
            "name": n["name"], "category": n["category"], "kind": n["kind"],
            "reason": n["reason"], "detail": n["detail"], "count": n["hits"],
            "requested": True,
        })
    res = telemetry.flush()
    if not res.get("ok"):
        return {"ok": False, "queued": len(pending),
                "error": res.get("reason", "delivery failed - queued for retry")}
    now = time.time()
    c = store.connect()
    c.executemany("UPDATE needs SET sent=1, sent_at=? WHERE id=?",
                  [(now, n["id"]) for n in pending])
    c.commit()
    return {"ok": True, "sent": len(pending)}
