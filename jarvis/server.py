#!/usr/bin/env python3
"""
server.py — the JARVIS API and UI host.

One HTTP server backs every surface: the desktop window loads its UI from here,
the headless server install serves the same UI to a browser, and paired nodes
call each other through the same endpoints. That is what makes "one interface"
literally true rather than three lookalike front-ends.

Auth: requests from loopback are trusted (the desktop window is loopback).
Anything arriving over the network must carry the shared bearer token, so
binding to 0.0.0.0 on a server install does not hand the machine away.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import (agents, config, installer, license, mobile, needs, nodes,
               scheduler as sched, store, telemetry, tools)
from .connectors import registry
from .connectors.chat import ChatProvider, strip_tool_call
from .connectors.hermes import Hermes
from .connectors.opencode import OpenCode
from .connectors.sandbox import Sandbox
from .connectors.slack import Slack
from .services.common import JsonHandler
from .supervisor import Supervisor

UI_DIR = Path(__file__).parent / "ui"


class App:
    """Shared application state. One instance per process."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or config.load()
        self.token = config.load_token()
        self.started_at = time.time()

        store.connect()
        telemetry.configure(self.cfg.get("telemetry", {}))
        for mod in (agents, registry, tools, needs, mobile):
            mod._init()
        nodes._init(self.cfg)

        host = self.cfg.get("stack_host", "127.0.0.1")
        svc = self.cfg.get("services", {})
        self.hermes = Hermes(host, svc.get("hermes", {}).get("port", 5562), self.token)
        self.sandbox = Sandbox(host, svc.get("sandbox", {}).get("port", 5561), self.token)
        self.chat = ChatProvider(self.cfg.get("chat", {}))
        self.slack = Slack(self.cfg.get("slack", {}).get("bot_token", ""),
                           self.cfg.get("slack", {}).get("default_channel", ""))
        self.opencode = OpenCode(self.cfg.get("opencode", {}))
        self.supervisor = Supervisor(host)
        self.scheduler = sched.Scheduler(self)

    # -- lifecycle ------------------------------------------------------------

    def boot(self) -> None:
        """Start what the customer should never have to start by hand."""
        if self.cfg.get("supervise", True):
            self.supervisor.start_all()
            self.supervisor.begin_watch()
        if telemetry.enabled():
            telemetry.begin_flusher()
        # Scheduled agents only fire while something is running to fire them.
        self.scheduler.start()
        store.log_event("app", f"JARVIS {config.VERSION} started")

    def shutdown(self) -> None:
        self.scheduler.stop()
        self.supervisor.stop_all()

    def reload_config(self, cfg: dict) -> None:
        self.cfg = cfg
        config.save(cfg)
        telemetry.configure(cfg.get("telemetry", {}))
        self.chat = ChatProvider(cfg.get("chat", {}))
        self.slack = Slack(cfg.get("slack", {}).get("bot_token", ""),
                           cfg.get("slack", {}).get("default_channel", ""))
        host = cfg.get("stack_host", "127.0.0.1")
        svc = cfg.get("services", {})
        self.hermes = Hermes(host, svc.get("hermes", {}).get("port", 5562), self.token)
        self.sandbox = Sandbox(host, svc.get("sandbox", {}).get("port", 5561), self.token)
        self.opencode = OpenCode(cfg.get("opencode", {}))

    @property
    def configured(self) -> bool:
        return bool(self.cfg.get("setup_complete"))

    # -- chat turn ------------------------------------------------------------

    def chat_turn(self, session_id: str, message: str) -> dict:
        """One chat turn, including a single round of tool execution."""
        store.add_message(session_id, "user", message)
        history = store.history_for_model(
            session_id, self.cfg.get("chat", {}).get("max_history", 30))

        system = self.cfg.get("chat", {}).get("system_prompt", "")
        tool_spec = tools.spec_for_model()
        if tool_spec:
            system = f"{system}\n\n{tool_spec}"

        res = self.chat.complete(history, system=system)
        text = res.get("text", "")
        call = res.get("tool_call")
        actions: list[dict] = []

        if call:
            outcome = self.run_tool_call(call)
            actions.append({"call": call, "result": outcome})
            # Feed the result back so the reply reflects what actually happened.
            follow = history + [
                {"role": "assistant", "content": text},
                {"role": "user",
                 "content": f"Tool result:\n{json.dumps(outcome)[:2000]}\n\n"
                            "Report what happened in one or two sentences."},
            ]
            second = self.chat.complete(follow, system=system, tools=False)
            text = f"{strip_tool_call(text)}\n\n{second.get('text', '')}".strip()

        store.add_message(session_id, "assistant", text,
                          meta={"actions": actions} if actions else None)
        return {"ok": res.get("ok", True), "reply": text, "actions": actions}

    def run_tool_call(self, call: dict) -> dict:
        """Execute one model-issued tool call."""
        name = call.get("tool", "")
        args = call.get("args", {}) or {}
        if name == "hermes.task":
            return self.hermes.dispatch(args.get("description", ""),
                                        args.get("priority", "normal"))
        if name == "sandbox.exec":
            return self.sandbox.exec(args.get("cmd", ""))
        if name == "slack.send":
            if not self.slack.configured:
                gap = needs.record("connector", "Slack", category="messaging",
                                   reason="Chat tried to post to Slack",
                                   source="agent")
                return {"ok": False, "error": "Slack is not connected.",
                        "report_id": gap.get("id")}
            return self.slack.send(args.get("channel", ""), args.get("text", ""))
        if name == "status.check":
            return {"ok": True, "services": self.supervisor.status()}
        if name == "tool.run":
            return tools.invoke(args.get("name", ""), args.get("args", {}),
                                sandbox=self.sandbox, agent_name="chat")
        if name == "opencode.run":
            if not self.opencode.installed:
                gap = needs.record("connector", "OpenCode", category="coding",
                                   reason="Chat tried to hand work to OpenCode",
                                   source="agent")
                return {"ok": False, "error": "OpenCode is not installed here.",
                        "report_id": gap.get("id")}
            return self.opencode.run(args.get("prompt", ""),
                                     directory=args.get("directory", ""),
                                     model=args.get("model", ""),
                                     session=args.get("session", ""))
        # Anything else is a tool the model wanted and we do not have.
        gap = needs.missing_tool(name, agent_name="chat",
                                 detail=f"Model requested it with args: {sorted(args)}")
        return {"ok": False, "error": f"No tool named {name!r}.",
                "missing_tool": True, "report_id": gap.get("id")}


APP: App | None = None

# -- routing -------------------------------------------------------------------

ROUTES: list[tuple[str, re.Pattern, str]] = []


def route(method: str, pattern: str):
    rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method.upper(), rx, fn.__name__))
        globals()[f"_h_{fn.__name__}"] = fn
        return fn

    return deco


# -- identity / bootstrap ------------------------------------------------------

@route("GET", "/api/node/hello")
def node_hello(app: App, _m, _q, _b) -> dict:
    """Unauthenticated so pairing can find a node; deliberately says nothing
    sensitive - name, platform, role, version."""
    info = config.host_info()
    return {"app": "jarvis", "version": config.VERSION,
            "hostname": info["hostname"], "platform": info["platform"],
            "role": "server" if app.cfg.get("bind") == "0.0.0.0" else "workstation",
            "configured": app.configured}


@route("GET", "/api/bootstrap")
def bootstrap(app: App, _m, _q, _b) -> dict:
    """Everything the UI needs for a first paint, in one round trip."""
    return {
        "ok": True,
        "app": {"name": config.APP_NAME, "version": config.VERSION,
                "started_at": app.started_at},
        "host": config.host_info(),
        "setup_complete": app.configured,
        "license": license.status(),
        "config": config.redacted(app.cfg),
        "services": app.supervisor.status(),
        "chat": app.chat.describe(),
        "slack": {"configured": app.slack.configured},
        "nodes": nodes.list_nodes(),
        "devices": mobile.summary(),
        "needs": needs.pending_count(),
        "telemetry": {"enabled": telemetry.enabled()},
        "install": installer.status(),
    }


@route("GET", "/api/status")
def api_status(app: App, _m, _q, _b) -> dict:
    return {
        "ok": True,
        "services": app.supervisor.status(),
        "hermes": app.hermes.status(),
        "sandbox": app.sandbox.status(),
        "chat": app.chat.describe(),
        "uptime": time.time() - app.started_at,
        "needs": needs.pending_count(),
        "devices": mobile.summary(),
    }


# -- setup ---------------------------------------------------------------------

@route("POST", "/api/setup")
def setup_save(app: App, _m, _q, body) -> dict:
    """Applies the first-run wizard. The customer's answers become the config;
    services start immediately so the app is usable when the wizard closes."""
    cfg = dict(app.cfg)
    for section in ("chat", "slack", "telemetry", "ui"):
        if section in body:
            cfg.setdefault(section, {}).update(body[section] or {})
    if body.get("stack_host"):
        cfg["stack_host"] = body["stack_host"]
    if body.get("role") == "server":
        cfg["bind"] = "0.0.0.0"
    cfg["supervise"] = bool(body.get("supervise", True))
    cfg["setup_complete"] = True
    app.reload_config(cfg)

    if cfg["supervise"]:
        app.supervisor.start_all()
        app.supervisor.begin_watch()
    if telemetry.enabled():
        telemetry.begin_flusher()
        telemetry.report("install.completed", {
            "platform": config.host_info()["platform"],
            "version": config.VERSION,
            "tier": license.status().get("tier", "trial"),
        })
    # Finish the install for them: put the icon where they expect it and, if
    # they asked, start JARVIS at login. Never fatal - a failed shortcut must
    # not turn a working setup into an error.
    install_result = None
    if body.get("install", True):
        try:
            install_result = installer.install(autostart=bool(body.get("autostart", True)))
        except Exception as e:  # noqa: BLE001 - report, never block setup
            install_result = {"ok": False, "error": str(e)}
            store.log_event("app", f"Desktop registration failed: {e}", level="error")

    store.log_event("app", "Setup completed")
    return {"ok": True, "config": config.redacted(cfg), "install": install_result}


@route("GET", "/api/install")
def install_get(_app, _m, _q, _b) -> dict:
    return {"ok": True, **installer.status()}


@route("POST", "/api/install")
def install_post(_app, _m, _q, body) -> dict:
    """Drives the Settings toggles for the icon and start-at-login."""
    action = body.get("action", "")
    if action == "install":
        return installer.install(autostart=bool(body.get("autostart", True)))
    if action == "uninstall":
        return installer.uninstall()
    if action == "autostart":
        return installer.set_autostart(bool(body.get("enabled")))
    return {"ok": False, "error": f"unknown action: {action}"}


@route("POST", "/api/setup/test")
def setup_test(app: App, _m, _q, body) -> dict:
    """Live 'does this actually work' check inside the wizard."""
    what = body.get("what", "")
    if what == "chat":
        probe = ChatProvider(body.get("chat", app.cfg.get("chat", {})))
        return {"ok": probe.ready(), "models": probe.available_models(),
                "provider": probe.provider}
    if what == "slack":
        return Slack(body.get("token", "")).auth_test()
    if what == "telemetry":
        url = body.get("ingest_url", "")
        return {"ok": bool(url), "note": "Reports will be sent here when you choose to send."}
    return {"ok": False, "error": f"unknown test: {what}"}


# -- licence -------------------------------------------------------------------

@route("GET", "/api/license")
def license_get(_app, _m, _q, _b) -> dict:
    return license.status()


@route("POST", "/api/license/activate")
def license_activate(app: App, _m, _q, body) -> dict:
    if body.get("key"):
        res = license.activate(body["key"])
    else:
        res = license.activate_online(
            app.cfg.get("activation_url", ""), body.get("reference", ""))
    if res.get("ok"):
        store.log_event("license", f"Activated: {res.get('tier')}")
        telemetry.report("license.activated", {"tier": res.get("tier")})
    return res


# -- chat ----------------------------------------------------------------------

@route("GET", "/api/sessions")
def sessions_list(_app, _m, _q, _b) -> dict:
    return {"ok": True, "sessions": store.list_sessions()}


@route("POST", "/api/sessions")
def sessions_create(_app, _m, _q, body) -> dict:
    return {"ok": True, "session": store.create_session(body.get("title", "New session"))}


@route("GET", "/api/sessions/<sid>/messages")
def session_messages(_app, m, _q, _b) -> dict:
    return {"ok": True, "messages": store.get_messages(m["sid"])}


@route("DELETE", "/api/sessions/<sid>")
def session_delete(_app, m, _q, _b) -> dict:
    store.delete_session(m["sid"])
    return {"ok": True}


@route("POST", "/api/chat")
def chat_send(app: App, _m, _q, body) -> dict:
    sid = body.get("session_id")
    msg = (body.get("message") or "").strip()
    if not msg:
        return {"ok": False, "error": "message required"}
    if not sid:
        sid = store.create_session(msg[:40])["id"]
    return {**app.chat_turn(sid, msg), "session_id": sid}


# -- agents --------------------------------------------------------------------

@route("GET", "/api/agents")
def agents_list(_app, _m, _q, _b) -> dict:
    return {"ok": True, "agents": agents.list_agents()}


@route("POST", "/api/agents")
def agents_create(_app, _m, _q, body) -> dict:
    if not (body.get("name") or "").strip():
        return {"ok": False, "error": "name required"}
    bad = _bad_schedule(body.get("schedule", ""))
    if bad:
        return bad
    return {"ok": True, "agent": agents.create(
        body["name"], body.get("role", ""), body.get("prompt", ""),
        body.get("connectors", []), body.get("schedule", ""))}


def _bad_schedule(text: str) -> dict | None:
    """Reject a schedule we cannot honour, rather than storing one that will
    never fire. A silently ignored setting is worse than a rejected one."""
    try:
        sched.parse(text)
    except sched.BadSchedule as e:
        return {"ok": False, "error": str(e), "field": "schedule"}
    return None


@route("PATCH", "/api/agents/<aid>")
def agents_update(_app, m, _q, body) -> dict:
    if "schedule" in body:
        bad = _bad_schedule(body.get("schedule", ""))
        if bad:
            return bad
    return {"ok": True, "agent": agents.update(m["aid"], **body)}


@route("GET", "/api/schedule")
def schedule_get(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "running": app.scheduler.running, **sched.status(app)}


@route("POST", "/api/schedule/tick")
def schedule_tick(app: App, _m, _q, _b) -> dict:
    """Fire anything due right now - used by the UI's 'run due now' button
    and by tests, so scheduling can be verified without waiting for a clock."""
    return {"ok": True, "fired": app.scheduler.tick()}


@route("DELETE", "/api/agents/<aid>")
def agents_delete(_app, m, _q, _b) -> dict:
    return {"ok": agents.delete(m["aid"])}


@route("POST", "/api/agents/<aid>/run")
def agents_run(app: App, m, _q, body) -> dict:
    return agents.run(m["aid"], body.get("input", ""), app.hermes)


@route("GET", "/api/agents/<aid>/runs")
def agents_runs(_app, m, _q, _b) -> dict:
    return {"ok": True, "runs": agents.runs(m["aid"])}


# -- hermes / sandbox ----------------------------------------------------------

@route("GET", "/api/hermes/tasks")
def hermes_tasks(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "tasks": app.hermes.tasks()}


@route("POST", "/api/hermes/task")
def hermes_task(app: App, _m, _q, body) -> dict:
    return app.hermes.dispatch(body.get("description", ""), body.get("priority", "normal"))


@route("GET", "/api/sandbox/files")
def sandbox_files(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "files": app.sandbox.files()}


@route("GET", "/api/sandbox/read")
def sandbox_read(app: App, _m, q, _b) -> dict:
    return app.sandbox.read(q.get("name", [""])[0])


@route("POST", "/api/sandbox/exec")
def sandbox_exec(app: App, _m, _q, body) -> dict:
    return app.sandbox.exec(body.get("cmd", ""), int(body.get("timeout", 60)))


@route("POST", "/api/sandbox/write")
def sandbox_write(app: App, _m, _q, body) -> dict:
    return app.sandbox.write(body.get("name", ""), body.get("content", ""))


# -- slack ---------------------------------------------------------------------

@route("GET", "/api/slack/channels")
def slack_channels(app: App, _m, _q, _b) -> dict:
    if not app.slack.configured:
        return {"ok": False, "error": "Slack is not connected.", "needs_setup": True}
    return {"ok": True, "channels": app.slack.channels()}


@route("GET", "/api/slack/history")
def slack_history(app: App, _m, q, _b) -> dict:
    if not app.slack.configured:
        return {"ok": False, "error": "Slack is not connected.", "needs_setup": True}
    return {"ok": True, "messages": app.slack.history(q.get("channel", [""])[0])}


@route("POST", "/api/slack/send")
def slack_send(app: App, _m, _q, body) -> dict:
    """Only ever reached by an explicit press of Send in the Slack pane."""
    if not app.slack.configured:
        return {"ok": False, "error": "Slack is not connected.", "needs_setup": True}
    return app.slack.send(body.get("channel", ""), body.get("text", ""),
                          body.get("thread_ts"))


# -- connectors ----------------------------------------------------------------

@route("GET", "/api/opencode")
def opencode_status(app: App, _m, _q, _b) -> dict:
    return {"ok": True, **app.opencode.status()}


@route("GET", "/api/opencode/models")
def opencode_models(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "models": app.opencode.models()}


@route("GET", "/api/opencode/sessions")
def opencode_sessions(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "sessions": app.opencode.sessions()}


@route("POST", "/api/opencode/run")
def opencode_run(app: App, _m, _q, body) -> dict:
    """Hand a task to the coding agent. Blocking - the UI shows a spinner."""
    return app.opencode.run(
        body.get("prompt", ""), directory=body.get("directory", ""),
        model=body.get("model", ""), session=body.get("session", ""),
        agent=body.get("agent", ""), timeout=body.get("timeout"))


@route("POST", "/api/opencode/queue")
def opencode_queue(app: App, _m, _q, body) -> dict:
    """Same job, run in the background so the caller is not held open.

    Deliberately NOT dispatched to Hermes: that daemon is a separate process
    with no OpenCode client, so a task handed to it would sit in the queue as
    an unexecuted note. The result lands in the activity log instead.
    """
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    if not app.opencode.installed:
        return {"ok": False, "error": "OpenCode is not installed here."}

    directory = body.get("directory", "")
    model = body.get("model", "")

    def work():
        store.log_event("opencode", f"Started: {prompt[:90]}")
        res = app.opencode.run(prompt, directory=directory, model=model)
        store.log_event(
            "opencode",
            (f"Finished in {res.get('duration')}s: {prompt[:70]}"
             if res.get("ok") else
             f"Failed: {res.get('error', 'unknown error')[:160]}"),
            level="info" if res.get("ok") else "error",
            meta={"output": (res.get("output") or "")[:4000]},
        )

    threading.Thread(target=work, daemon=True, name="opencode-job").start()
    return {"ok": True, "queued": True,
            "note": "Running in the background - watch System > Activity."}


@route("GET", "/api/connectors")
def connectors_list(app: App, _m, _q, _b) -> dict:
    return {"ok": True,
            "catalog": registry.catalog(app.cfg, app.supervisor, app.slack, app.chat,
                                        app.opencode),
            "custom": registry.list_connectors()}


@route("POST", "/api/connectors")
def connectors_create(_app, _m, _q, body) -> dict:
    if not (body.get("name") or "").strip():
        return {"ok": False, "error": "name required"}
    return {"ok": True, "connector": registry.create(
        body["name"], body.get("base_url", ""), body.get("kind", "http"),
        body.get("auth_type", "none"), body.get("auth_value", ""),
        body.get("headers"), body.get("test_path", ""), body.get("notes", ""))}


@route("PATCH", "/api/connectors/<cid>")
def connectors_update(_app, m, _q, body) -> dict:
    return {"ok": True, "connector": registry.update(m["cid"], **body)}


@route("DELETE", "/api/connectors/<cid>")
def connectors_delete(_app, m, _q, _b) -> dict:
    return {"ok": registry.delete(m["cid"])}


@route("POST", "/api/connectors/<cid>/test")
def connectors_test(_app, m, _q, _b) -> dict:
    return registry.test(m["cid"])


# -- tools ---------------------------------------------------------------------

@route("GET", "/api/tools")
def tools_list(_app, _m, _q, _b) -> dict:
    return {"ok": True, "tools": tools.list_tools()}


@route("POST", "/api/tools")
def tools_create(_app, _m, _q, body) -> dict:
    return tools.create(body.get("name", ""), body.get("kind", "http"),
                        body.get("description", ""), body.get("spec"),
                        body.get("connector_id", ""))


@route("PATCH", "/api/tools/<tid>")
def tools_update(_app, m, _q, body) -> dict:
    return {"ok": True, "tool": tools.update(m["tid"], **body)}


@route("DELETE", "/api/tools/<tid>")
def tools_delete(_app, m, _q, _b) -> dict:
    return {"ok": tools.delete(m["tid"])}


@route("POST", "/api/tools/invoke")
def tools_invoke(app: App, _m, _q, body) -> dict:
    return tools.invoke(body.get("name", ""), body.get("args", {}),
                        sandbox=app.sandbox, agent_name=body.get("agent", ""))


# -- needs (optional reports) --------------------------------------------------

@route("GET", "/api/needs")
def needs_list(_app, _m, q, _b) -> dict:
    return {"ok": True, "needs": needs.list_needs(q.get("kind", [""])[0]),
            "pending": needs.pending_count(),
            "can_send": telemetry.enabled()}


@route("POST", "/api/needs")
def needs_create(_app, _m, _q, body) -> dict:
    return needs.record(body.get("kind", "tool"), body.get("name", ""),
                        body.get("category", ""), body.get("reason", ""),
                        body.get("detail", ""))


@route("GET", "/api/needs/<nid>/preview")
def needs_preview(_app, m, _q, _b) -> dict:
    return needs.preview(m["nid"])


@route("POST", "/api/needs/<nid>/send")
def needs_send(_app, m, _q, _b) -> dict:
    return needs.send(m["nid"])


@route("POST", "/api/needs/send-all")
def needs_send_all(_app, _m, _q, body) -> dict:
    return needs.send_all(body.get("kind", ""))


@route("DELETE", "/api/needs/<nid>")
def needs_delete(_app, m, _q, _b) -> dict:
    return {"ok": needs.delete(m["nid"])}


# -- services ------------------------------------------------------------------

@route("GET", "/api/services")
def services_list(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "services": app.supervisor.status()}


@route("POST", "/api/services/<sid>/<action>")
def services_action(app: App, m, _q, _b) -> dict:
    svc = app.supervisor.get(m["sid"])
    if not svc:
        return {"ok": False, "error": "unknown service"}
    action = m["action"]
    if action == "start":
        return svc.start()
    if action == "stop":
        return svc.stop()
    if action == "restart":
        return svc.restart()
    return {"ok": False, "error": f"unknown action: {action}"}


@route("GET", "/api/services/<sid>/logs")
def services_logs(app: App, m, _q, _b) -> dict:
    svc = app.supervisor.get(m["sid"])
    if not svc:
        return {"ok": False, "error": "unknown service"}
    return {"ok": True, "log": svc.tail()}


# -- nodes ---------------------------------------------------------------------

@route("GET", "/api/nodes")
def nodes_list(_app, _m, _q, _b) -> dict:
    return {"ok": True, "nodes": nodes.ping_all()}


@route("POST", "/api/nodes")
def nodes_add(_app, _m, _q, body) -> dict:
    return nodes.add(body.get("name", ""), body.get("url", ""),
                     body.get("token", ""), body.get("role", "workstation"))


@route("DELETE", "/api/nodes/<nid>")
def nodes_remove(_app, m, _q, _b) -> dict:
    return {"ok": nodes.remove(m["nid"])}


@route("POST", "/api/nodes/discover")
def nodes_discover(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "found": nodes.discover(app.cfg.get("ui_port", 5580))}


@route("POST", "/api/nodes/<nid>/proxy")
def nodes_proxy(_app, m, _q, body) -> dict:
    return nodes.proxy(m["nid"], body.get("method", "GET"),
                       body.get("path", "/api/status"), body.get("body"))


# -- mobile devices ------------------------------------------------------------
#
# The phone apps talk to these. Every route is token-authenticated like the
# rest of the API; the handset holds the same shared bearer token, entered once
# during pairing.

@route("POST", "/api/mobile/register")
def mobile_register(_app, _m, _q, body) -> dict:
    return mobile.register(
        body.get("platform", ""), body.get("name", ""), body.get("model", ""),
        body.get("os_version", ""), body.get("app_version", ""),
        body.get("push_token", ""), body.get("device_id", ""))


@route("POST", "/api/mobile/<did>/heartbeat")
def mobile_heartbeat(_app, m, _q, body) -> dict:
    return mobile.heartbeat(
        m["did"], body.get("battery"), body.get("network", ""),
        body.get("app_version", ""), body.get("error", ""))


@route("POST", "/api/mobile/commands/<cid>/result")
def mobile_command_result(_app, m, _q, body) -> dict:
    return mobile.complete(m["cid"], bool(body.get("ok")), body.get("result", ""))


@route("GET", "/api/mobile/devices")
def mobile_devices(_app, _m, q, _b) -> dict:
    return {"ok": True, "devices": mobile.list_devices(q.get("platform", [""])[0]),
            "summary": mobile.summary(), "commands": mobile.COMMANDS}


@route("GET", "/api/mobile/devices/<did>")
def mobile_device_get(_app, m, _q, _b) -> dict:
    dev = mobile.get(m["did"])
    if not dev:
        return {"ok": False, "error": "unknown device"}
    return {"ok": True, "device": dev, "commands": mobile.commands_for(m["did"])}


@route("DELETE", "/api/mobile/devices/<did>")
def mobile_device_remove(_app, m, _q, _b) -> dict:
    return {"ok": mobile.remove(m["did"])}


@route("POST", "/api/mobile/devices/<did>/approve")
def mobile_device_approve(_app, m, _q, body) -> dict:
    return {"ok": True, "device": mobile.set_approved(m["did"],
                                                      bool(body.get("approved", True)))}


@route("POST", "/api/mobile/devices/<did>/command")
def mobile_device_command(_app, m, _q, body) -> dict:
    return mobile.enqueue(m["did"], body.get("action", ""), body.get("args"),
                          body.get("issued_by", "user"))


# -- settings / events / telemetry ---------------------------------------------

@route("GET", "/api/settings")
def settings_get(app: App, _m, _q, _b) -> dict:
    return {"ok": True, "config": config.redacted(app.cfg)}


@route("POST", "/api/settings")
def settings_save(app: App, _m, _q, body) -> dict:
    cfg = dict(app.cfg)
    for section in ("chat", "slack", "telemetry", "ui", "services"):
        if section in body:
            incoming = body[section] or {}
            # A masked secret coming back from the UI means "unchanged".
            for k, v in list(incoming.items()):
                if isinstance(v, str) and v.endswith("…") or v == "set":
                    incoming.pop(k)
            cfg.setdefault(section, {}).update(incoming)
    for key in ("stack_host", "bind", "ui_port", "supervise", "activation_url"):
        if key in body:
            cfg[key] = body[key]
    app.reload_config(cfg)
    return {"ok": True, "config": config.redacted(cfg)}


@route("GET", "/api/events")
def events_list(_app, _m, q, _b) -> dict:
    try:
        since = float(q.get("since", ["0"])[0])
    except ValueError:
        since = 0.0
    return {"ok": True, "events": store.recent_events(since=since)}


@route("GET", "/api/telemetry/preview")
def telemetry_preview(_app, _m, _q, _b) -> dict:
    return {"ok": True, "enabled": telemetry.enabled(), "queued": telemetry.preview()}


@route("POST", "/api/telemetry/flush")
def telemetry_flush(_app, _m, _q, _b) -> dict:
    return telemetry.flush()


@route("POST", "/api/telemetry/clear")
def telemetry_clear(_app, _m, _q, _b) -> dict:
    telemetry.clear()
    return {"ok": True}


# -- HTTP handler --------------------------------------------------------------

class Handler(JsonHandler):
    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if method == "GET" and not path.startswith("/api/"):
            return self._serve_ui(path)

        # Loopback is trusted; anything else must present the token.
        if path != "/api/node/hello" and not self._local() and not self._authed():
            return self._json(401, {"error": "unauthorized"})

        query = urllib.parse.parse_qs(parsed.query)
        body = self._body() if method in ("POST", "PATCH", "PUT") else {}

        for m, rx, fname in ROUTES:
            if m != method:
                continue
            match = rx.match(path)
            if not match:
                continue
            fn = globals()[f"_h_{fname}"]
            try:
                result = fn(APP, match.groupdict(), query, body)
            except Exception as e:  # a broken endpoint must not kill the app
                store.log_event("api", f"{path} failed: {e}", level="error")
                return self._json(500, {"ok": False, "error": str(e)})
            return self._json(200, result if result is not None else {"ok": True})

        self._json(404, {"error": f"no route for {method} {path}"})

    def _local(self) -> bool:
        addr = self.client_address[0] if self.client_address else ""
        return addr in ("127.0.0.1", "::1", "localhost")

    def _serve_ui(self, path: str) -> None:
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            return self._json(403, {"error": "forbidden"})
        if not target.is_file():
            target = UI_DIR / "index.html"  # single-page app fallback
            if not target.is_file():
                return self._json(404, {"error": "UI not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


class JarvisHTTPServer(ThreadingHTTPServer):
    # SO_REUSEADDR means opposite things on the two platforms, so this has to
    # be decided per platform rather than picked once:
    #
    #   Windows  a SECOND process may bind a port already in use, silently
    #            splitting traffic between two instances. A desktop app gets
    #            launched twice constantly, so refuse it.
    #   POSIX    it only permits rebinding a socket in TIME_WAIT; it cannot
    #            steal a live one. Refusing it means a service restart fails
    #            with EADDRINUSE until TIME_WAIT expires - which is exactly
    #            what broke `systemctl restart` on the Ubuntu box.
    allow_reuse_address = (os.name == "posix")


def already_running(port: int, host: str = "127.0.0.1") -> bool:
    """True when a JARVIS instance is already serving this port."""
    from .connectors import base
    if not base.port_open(host, port, timeout=0.5):
        return False
    try:
        d = base.get(f"http://{host}:{port}/api/node/hello", timeout=2)
        return isinstance(d, dict) and d.get("app") == "jarvis"
    except base.HttpError:
        return False


def serve(app: App, port: int | None = None, bind: str | None = None,
          background: bool = False) -> ThreadingHTTPServer:
    global APP
    APP = app
    Handler.token = app.token
    port = port or app.cfg.get("ui_port", 5580)
    bind = bind or app.cfg.get("bind", "127.0.0.1")

    srv = JarvisHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    print(f"[jarvis] UI and API on http://{bind}:{port}", flush=True)

    if background:
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name="jarvis-http").start()
        return srv
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        srv.server_close()
    return srv
