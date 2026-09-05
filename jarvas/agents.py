#!/usr/bin/env python3
"""
agents.py — customer-facing agent creation and dispatch.

Customers build their own agents in the JARVAS UI: a name, a role prompt,
which connectors it may touch, and an optional schedule. A run sends that
brief to the model with tools available, on a worker thread so a slow agent
never blocks the UI.

Every agent a customer creates is a signal about what the product is missing.
Definitions are handed to telemetry (with consent) so the CrossPCAI workforce
can build the connectors and tools those agents are reaching for.
"""

from __future__ import annotations

import json
import threading
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
    # The one agent that changes code. It does not edit anything itself - it
    # briefs OpenCode, which has the editing tools and its own permission
    # prompts, and then reports back what actually changed.
    ("coder", "Coder", "Hands real coding work to OpenCode on this machine",
     "You turn a request into a precise brief for the OpenCode agent running "
     "on this machine, using the opencode.run tool. State the working "
     "directory and exactly what should change. Prefer one focused task over "
     "a sweeping one. When OpenCode reports back, summarise what it actually "
     "changed - files touched, commands run - and say plainly if it failed or "
     "stopped early rather than implying the work is done. If OpenCode is not "
     "installed or its model credentials are broken, say so and record it in "
     "Reports instead of falling back to blind shell commands."),
    # One fleet agent per mobile platform. They are separate rather than a
    # single "Mobile" agent because the two stores, release cadences and crash
    # formats have almost nothing in common — a merged brief would be vague in
    # both directions.
    ("android-fleet", "Android Fleet", "Watches paired Android handsets",
     "You manage the Android side of the JARVAS mobile fleet. Read the device "
     "list from /api/mobile/devices?platform=android. Report handsets that have "
     "gone stale, are stuck on an old app_version, or are reporting last_error. "
     "For anything you can fix from here, queue a command (ping, refresh, "
     "collect_logs, report_needs, update_check) rather than asking the customer "
     "to touch the phone. Never queue sign_out unless a human asked for it — it "
     "un-pairs the device. When a handset needs a connector or capability the "
     "app does not have, record it in Reports instead of inventing a workaround."),
    ("ios-fleet", "iOS Fleet", "Watches paired iPhones and iPads",
     "You manage the iOS side of the JARVAS mobile fleet. Read the device list "
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
    # Seed each built-in independently. Checking "is the table empty" instead
    # would mean a version that ships a new built-in agent never delivers it to
    # anyone who already had JARVAS installed.
    existing = {r["id"] for r in c.execute("SELECT id FROM agents").fetchall()}
    for aid, name, role, prompt in BUILTIN:
        if aid not in existing:
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

def run(aid: str, task_input: str, app) -> dict:
    """Actually run an agent: its brief goes to the model, with tools.

    This used to post the brief to the Hermes queue. That daemon executes only
    strings beginning with `$`, so every agent run was filed as an unread note
    and nothing ever thought about it - the agents looked busy and did nothing.
    The model call happens on a worker thread so a slow agent never blocks the
    UI, which was the only real reason to involve the queue.
    """
    agent = get(aid)
    if not agent:
        return {"ok": False, "error": "agent not found"}
    if not agent["enabled"]:
        return {"ok": False, "error": "agent is disabled"}

    now = time.time()
    c = store.connect()
    cur = c.execute(
        "INSERT INTO agent_runs (agent_id,task_id,input,status,created_at) "
        "VALUES (?,?,?,?,?)", (aid, None, task_input, "running", now))
    run_id = cur.lastrowid
    c.execute("UPDATE agents SET last_run=?, run_count=run_count+1 WHERE id=?", (now, aid))
    c.commit()

    threading.Thread(target=_execute, args=(agent, task_input, app, run_id),
                     daemon=True, name=f"agent-{aid}").start()
    return {"ok": True, "agent": agent["name"], "run_id": run_id, "status": "running"}


def _execute(agent: dict, task_input: str, app, run_id: int) -> None:
    """One agent turn: model, optional tool call, then the model's summary."""
    from .connectors.chat import strip_tool_call

    system = agent["prompt"] or f"You are {agent['name']}. {agent['role']}"
    try:
        from . import tools as tools_mod
        spec = tools_mod.spec_for_model()
        if spec:
            system = f"{system}\n\n{spec}"
    except Exception:  # noqa: BLE001 - a tool listing failure must not stop the run
        pass

    messages = [{"role": "user", "content": task_input or
                 "Carry out your standing brief and report what you found."}]
    status, summary, actions = "done", "", []

    try:
        res = app.chat.complete(messages, system=system)
        text = res.get("text", "")
        call = res.get("tool_call")

        if call:
            outcome = app.run_tool_call(call)
            actions.append({"tool": call.get("tool"), "ok": outcome.get("ok")})
            follow = messages + [
                {"role": "assistant", "content": text},
                {"role": "user",
                 "content": f"Tool result:\n{json.dumps(outcome)[:2000]}\n\n"
                            "Summarise what actually happened. If it failed, say so."},
            ]
            second = app.chat.complete(follow, system=system, tools=False)
            text = f"{strip_tool_call(text)}\n\n{second.get('text', '')}".strip()

        summary = text
        if not res.get("ok"):
            status = "failed"
    except Exception as e:  # noqa: BLE001 - record the failure, never crash the thread
        status, summary = "failed", f"{type(e).__name__}: {e}"

    c = store.connect()
    c.execute("UPDATE agent_runs SET status=?, result=? WHERE id=?",
              (status, json.dumps({"summary": summary[:8000], "actions": actions}), run_id))
    c.commit()
    store.log_event(
        "agents",
        (f"{agent['name']} finished" if status == "done"
         else f"{agent['name']} failed: {summary[:150]}"),
        level="info" if status == "done" else "error",
        meta={"agent": agent["id"], "run": run_id},
    )


def runs(aid: str, limit: int = 50) -> list[dict]:
    rows = store.connect().execute(
        "SELECT * FROM agent_runs WHERE agent_id=? ORDER BY id DESC LIMIT ?", (aid, limit)
    ).fetchall()
    return [dict(r) for r in rows]
