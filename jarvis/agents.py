#!/usr/bin/env python3
"""
agents.py — customer-facing agent creation and dispatch.

Customers build their own agents in the JARVIS UI: a name, a role prompt,
which connectors it may touch, and an optional schedule. Agents run by
dispatching work to the Hermes queue, so a slow agent never blocks the UI.

Every agent a customer creates is a signal about what the product is missing.
Definitions are handed to telemetry (with consent) so the CrossPCAI workforce
can build the connectors and tools those agents are reaching for.
"""

from __future__ import annotations

import json
import time
import uuid

from . import store, telemetry

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    prompt       TEXT NOT NULL DEFAULT '',
    connectors   TEXT NOT NULL DEFAULT '[]',
    schedule     TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    builtin      INTEGER NOT NULL DEFAULT 0,
    last_run     REAL,
    run_count    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL,
    task_id    TEXT,
    input      TEXT,
    status     TEXT NOT NULL DEFAULT 'queued',
    result     TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON agent_runs(agent_id, id DESC);
"""

# Seeded on first run so a new customer opens the app to a working workforce
# rather than an empty list.
BUILTIN = [
    ("ops", "Daily Ops", "Morning briefing, service health, anomaly watch",
     "Summarise overnight activity, flag anything that failed, and list the "
     "three things that need a human decision today."),
    ("support", "Customer Support", "Triage and answer inbound support",
     "Classify each inbound request, answer what you can from the knowledge "
     "base, and escalate anything involving billing or data loss."),
    ("sales", "Sales Outreach", "Sequenced outreach and reply detection",
     "Draft outreach for new leads, watch for replies, and hand warm replies "
     "to a human with the full thread context."),
    # One fleet agent per mobile platform. They are separate rather than a
    # single "Mobile" agent because the two stores, release cadences and crash
    # formats have almost nothing in common — a merged brief would be vague in
    # both directions.
    ("android-fleet", "Android Fleet", "Watches paired Android handsets",
     "You manage the Android side of the JARVIS mobile fleet. Read the device "
     "list from /api/mobile/devices?platform=android. Report handsets that have "
     "gone stale, are stuck on an old app_version, or are reporting last_error. "
     "For anything you can fix from here, queue a command (ping, refresh, "
     "collect_logs, report_needs, update_check) rather than asking the customer "
     "to touch the phone. Never queue sign_out unless a human asked for it — it "
     "un-pairs the device. When a handset needs a connector or capability the "
     "app does not have, record it in Reports instead of inventing a workaround."),
    ("ios-fleet", "iOS Fleet", "Watches paired iPhones and iPads",
     "You manage the iOS side of the JARVIS mobile fleet. Read the device list "
     "from /api/mobile/devices?platform=ios. iOS suspends background work "
     "aggressively, so treat a gap in heartbeats as normal unless it outlasts a "
     "working day — flag sustained silence, not every miss. Watch for handsets "
     "left on an old app_version after a release, and for last_error values that "
     "repeat across devices, which usually means a build problem rather than a "
     "handset problem. Queue commands (ping, refresh, collect_logs, "
     "report_needs, update_check) rather than asking the customer to touch the "
     "device; never queue sign_out unless a human asked for it."),
]


def _init() -> None:
    c = store.connect()
    c.executescript(SCHEMA)
    c.commit()
    if not c.execute("SELECT 1 FROM agents LIMIT 1").fetchone():
        for aid, name, role, prompt in BUILTIN:
            create(name, role, prompt, builtin=True, agent_id=aid, notify=False)


def _row(r) -> dict:
    d = dict(r)
    d["connectors"] = json.loads(d.get("connectors") or "[]")
    d["enabled"] = bool(d["enabled"])
    d["builtin"] = bool(d["builtin"])
    return d


# ── CRUD ──────────────────────────────────────────────────────────────────────

def create(name: str, role: str = "", prompt: str = "",
           connectors: list[str] | None = None, schedule: str = "",
           builtin: bool = False, agent_id: str | None = None,
           notify: bool = True) -> dict:
    aid = agent_id or uuid.uuid4().hex[:10]
    now = time.time()
    c = store.connect()
    c.execute(
        "INSERT OR REPLACE INTO agents "
        "(id,name,role,prompt,connectors,schedule,enabled,builtin,run_count,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,1,?,0,?,?)",
        (aid, name, role, prompt, json.dumps(connectors or []), schedule,
         1 if builtin else 0, now, now),
    )
    c.commit()
    agent = get(aid)
    if notify and not builtin:
        # A customer-authored agent tells us what they actually need.
        telemetry.report("agent.created", {
            "name": name, "role": role,
            "connectors": connectors or [],
            "prompt_length": len(prompt or ""),
            "has_schedule": bool(schedule),
        })
    store.log_event("agents", f"Agent created: {name}", meta={"id": aid})
    return agent


def get(aid: str) -> dict | None:
    r = store.connect().execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()
    return _row(r) if r else None


def list_agents() -> list[dict]:
    rows = store.connect().execute(
        "SELECT * FROM agents ORDER BY builtin DESC, name"
    ).fetchall()
    return [_row(r) for r in rows]


def update(aid: str, **fields) -> dict | None:
    allowed = {"name", "role", "prompt", "connectors", "schedule", "enabled"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "connectors":
            v = json.dumps(v or [])
        if k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get(aid)
    sets.append("updated_at=?")
    vals.extend([time.time(), aid])
    c = store.connect()
    c.execute(f"UPDATE agents SET {','.join(sets)} WHERE id=?", vals)
    c.commit()
    return get(aid)


def delete(aid: str) -> bool:
    c = store.connect()
    cur = c.execute("DELETE FROM agents WHERE id=? AND builtin=0", (aid,))
    c.commit()
    return cur.rowcount > 0


# ── Running ───────────────────────────────────────────────────────────────────

def run(aid: str, task_input: str, hermes) -> dict:
    """Dispatch one agent run onto the Hermes queue."""
    agent = get(aid)
    if not agent:
        return {"ok": False, "error": "agent not found"}
    if not agent["enabled"]:
        return {"ok": False, "error": "agent is disabled"}

    brief = f"{agent['prompt']}\n\nTask: {task_input}".strip() if agent["prompt"] else task_input
    res = hermes.dispatch(brief, priority="normal", agent_id=aid)

    now = time.time()
    c = store.connect()
    c.execute(
        "INSERT INTO agent_runs (agent_id,task_id,input,status,created_at) VALUES (?,?,?,?,?)",
        (aid, res.get("id"), task_input, "queued" if res.get("ok") else "failed", now),
    )
    c.execute("UPDATE agents SET last_run=?, run_count=run_count+1 WHERE id=?", (now, aid))
    c.commit()
    store.log_event("agents", f"{agent['name']} dispatched a task",
                    meta={"agent": aid, "task": res.get("id")})
    return {"ok": res.get("ok", False), "agent": agent["name"],
            "task_id": res.get("id"), "error": res.get("error")}


def runs(aid: str, limit: int = 50) -> list[dict]:
    rows = store.connect().execute(
        "SELECT * FROM agent_runs WHERE agent_id=? ORDER BY id DESC LIMIT ?", (aid, limit)
    ).fetchall()
    return [dict(r) for r in rows]
