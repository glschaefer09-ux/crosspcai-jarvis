#!/usr/bin/env python3
"""
tools.py — customer-built tools that agents can call.

A tool is a named, reusable action. Three kinds cover almost everything a
customer asks for without waiting on a release:

    http    call a URL, optionally through one of their connectors
    shell   run a command template in the sandbox
    prompt  a saved instruction the model expands (a macro)

Templates interpolate {input} and any named argument, so one tool serves many
calls. If an agent invokes a tool that is not defined, `invoke()` records the
gap through needs.missing_tool() - locally - and tells the caller a report is
available to send. Nothing is transmitted by that.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from . import needs, store, telemetry
from .connectors import registry

KINDS = ("http", "shell", "prompt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL DEFAULT 'http',
    description  TEXT NOT NULL DEFAULT '',
    spec         TEXT NOT NULL DEFAULT '{}',
    connector_id TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    call_count   INTEGER NOT NULL DEFAULT 0,
    last_used    REAL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
"""

_VAR = re.compile(r"\{(\w+)\}")


def _init() -> None:
    c = store.connect()
    c.executescript(SCHEMA)
    c.commit()


def _row(r) -> dict:
    d = dict(r)
    try:
        d["spec"] = json.loads(d.get("spec") or "{}")
    except json.JSONDecodeError:
        d["spec"] = {}
    d["enabled"] = bool(d["enabled"])
    d["variables"] = sorted(set(_VAR.findall(json.dumps(d["spec"]))))
    return d


def _fill(template: str, args: dict) -> str:
    """Substitute {name} placeholders. Unknown names are left intact so a
    half-filled template is visible rather than silently mangled."""
    def sub(m):
        return str(args.get(m.group(1), m.group(0)))
    return _VAR.sub(sub, template or "")


# -- CRUD ---------------------------------------------------------------------

def create(name: str, kind: str = "http", description: str = "",
           spec: dict | None = None, connector_id: str = "") -> dict:
    if kind not in KINDS:
        return {"ok": False, "error": f"kind must be one of {', '.join(KINDS)}"}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}

    tid = uuid.uuid4().hex[:10]
    now = time.time()
    c = store.connect()
    try:
        c.execute(
            "INSERT INTO tools (id,name,kind,description,spec,connector_id,enabled,"
            "call_count,created_at,updated_at) VALUES (?,?,?,?,?,?,1,0,?,?)",
            (tid, name, kind, description, json.dumps(spec or {}), connector_id, now, now),
        )
        c.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            return {"ok": False, "error": f"a tool named {name!r} already exists"}
        return {"ok": False, "error": str(e)}

    # A tool the customer had to build themselves is a gap in the product.
    telemetry.report("tool.created", {
        "name": name, "kind": kind, "connector": connector_id,
        "fields": sorted((spec or {}).keys()),
    })
    store.log_event("tools", f"Tool created: {name}", meta={"id": tid})
    return {"ok": True, **(get(tid) or {})}


def get(tid: str) -> dict | None:
    r = store.connect().execute("SELECT * FROM tools WHERE id=?", (tid,)).fetchone()
    return _row(r) if r else None


def by_name(name: str) -> dict | None:
    r = store.connect().execute(
        "SELECT * FROM tools WHERE lower(name)=lower(?)", (name,)
    ).fetchone()
    return _row(r) if r else None


def list_tools(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM tools"
    if enabled_only:
        sql += " WHERE enabled=1"
    sql += " ORDER BY name"
    return [_row(r) for r in store.connect().execute(sql).fetchall()]


def update(tid: str, **fields) -> dict | None:
    allowed = {"name", "kind", "description", "spec", "connector_id", "enabled"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "spec":
            v = json.dumps(v or {})
        if k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get(tid)
    sets.append("updated_at=?")
    vals.extend([time.time(), tid])
    c = store.connect()
    c.execute(f"UPDATE tools SET {','.join(sets)} WHERE id=?", vals)
    c.commit()
    return get(tid)


def delete(tid: str) -> bool:
    c = store.connect()
    cur = c.execute("DELETE FROM tools WHERE id=?", (tid,))
    c.commit()
    return cur.rowcount > 0


# -- invocation ---------------------------------------------------------------

def invoke(name: str, args: dict | None = None, *, sandbox=None,
           agent_name: str = "") -> dict:
    """Run a tool by name.

    When the tool does not exist, this is not just an error: it is the clearest
    signal the product is missing something. The gap is recorded locally and the
    caller gets `report_id` so the UI can offer an optional Send report.
    """
    args = args or {}
    tool = by_name(name)
    if not tool:
        gap = needs.missing_tool(
            name, agent_name=agent_name,
            detail=f"Called with: {', '.join(sorted(args)) or 'no arguments'}",
        )
        return {
            "ok": False,
            "error": f"No tool named {name!r} is available.",
            "missing_tool": True,
            "report_id": gap.get("id"),
            "report_hint": "You can send a report asking CrossPCAI to build it.",
        }
    if not tool["enabled"]:
        return {"ok": False, "error": f"Tool {name!r} is disabled."}

    spec = tool["spec"]
    kind = tool["kind"]
    result: dict

    if kind == "http":
        path = _fill(spec.get("path", ""), args)
        body = None
        if spec.get("body"):
            raw = _fill(json.dumps(spec["body"]), args)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"raw": raw}
        method = (spec.get("method") or "GET").upper()
        if tool["connector_id"]:
            result = registry.call(tool["connector_id"], path, method, body)
        else:
            from .connectors import base
            url = _fill(spec.get("url", ""), args)
            if not url:
                result = {"ok": False, "error": "tool has neither a connector nor a url"}
            else:
                try:
                    data = base.request(method, url, json_body=body,
                                        headers=spec.get("headers") or {}, timeout=30)
                    result = {"ok": True, "data": data}
                except base.HttpError as e:
                    result = {"ok": False, "error": str(e)}

    elif kind == "shell":
        cmd = _fill(spec.get("command", ""), args)
        if not cmd:
            result = {"ok": False, "error": "tool has no command"}
        elif sandbox is None:
            result = {"ok": False, "error": "sandbox unavailable"}
        else:
            result = sandbox.exec(cmd, timeout=int(spec.get("timeout", 60)))

    else:  # prompt
        result = {"ok": True, "prompt": _fill(spec.get("template", ""), args)}

    c = store.connect()
    c.execute("UPDATE tools SET call_count=call_count+1, last_used=? WHERE id=?",
              (time.time(), tool["id"]))
    c.commit()
    return {"tool": tool["name"], "kind": kind, **result}


def spec_for_model(enabled_only: bool = True) -> str:
    """Human-readable tool list injected into the chat system prompt so the
    model only offers tools that actually exist on this machine."""
    tools = list_tools(enabled_only=enabled_only)
    if not tools:
        return ""
    lines = ["Custom tools available on this machine:"]
    for t in tools:
        args = ", ".join(t["variables"]) or "no arguments"
        desc = t["description"] or t["kind"]
        lines.append(f'  tool.run  {{"name": "{t["name"]}", "args": {{{args}}}}}  - {desc}')
    lines.append(
        "If you need a tool that is not listed, say so plainly - the customer "
        "can send a report asking for it to be built."
    )
    return "\n".join(lines)
