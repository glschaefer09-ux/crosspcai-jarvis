#!/usr/bin/env python3
"""
config.py — JARVAS configuration, paths and shared auth token.

Reuses the existing CrossPCAI conventions:
  ~/.crosspcai/agent_token   shared bearer token for hermes/sandbox/agent-api
  ~/.crosspcai/jarvas.json   JARVAS-specific settings
  ~/.crosspcai/jarvas.db     chat sessions + event log
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import socket
from pathlib import Path

# Brand. APP_NAME is what a person reads; SHORT_NAME is what goes in filenames,
# shortcuts and the binary, because a space in a path is a nuisance on every
# platform. Renamed from the previous working title, which was a Marvel
# trademark and not defensible for a commercial AI product.
APP_NAME = "Jarvas Orus"
SHORT_NAME = "Jarvas"
APP_ID = "ai.crosspc.jarvas"
VERSION = "1.0.0"

# ~/.crosspcai deliberately keeps its name: it holds the bearer token shared
# with the Hermes and sandbox daemons, so renaming it would break attaching to
# an existing CrossPCAI stack. It is a directory name, not a brand.
CONFIG_DIR = Path(os.environ.get("CROSSPCAI_HOME", Path.home() / ".crosspcai"))
TOKEN_FILE = CONFIG_DIR / "agent_token"
CONFIG_FILE = CONFIG_DIR / "jarvas.json"
DB_FILE = CONFIG_DIR / "jarvas.db"
LOG_FILE = CONFIG_DIR / "jarvas.log"

# Files the pre-rename build wrote. Existing installs carry real work in them -
# agents, chat history, licences - so they are moved across on first run rather
# than silently abandoned.
_LEGACY = [("jarvis.json", CONFIG_FILE), ("jarvis.db", DB_FILE),
           ("jarvis.log", LOG_FILE)]

# Ports of the existing CrossPCAI stack. JARVAS never re-implements these
# services — it is the single front-end that drives them.
DEFAULTS: dict = {
    "ui_port": int(os.environ.get("JARVAS_PORT", "5580")),
    "bind": os.environ.get("JARVAS_BIND", "127.0.0.1"),
    # Host running the CrossPCAI daemons. "127.0.0.1" for a local install;
    # point at another machine on the LAN to drive it from a laptop.
    "stack_host": os.environ.get("CROSSPCAI_HOST", "127.0.0.1"),
    "services": {
        "hermes": {"port": 5562, "label": "Hermes"},
        "sandbox": {"port": 5561, "label": "Sandbox"},
        "app": {"port": 5570, "label": "App"},
        "agent_api": {"port": 5599, "label": "Agent API"},
    },
    "chat": {
        # provider: ollama | anthropic | openai
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "llama3.2:latest",
        "anthropic_model": "claude-opus-5",
        "openai_model": "gpt-4o-mini",
        "system_prompt": (
            "You are JARVAS, the operations assistant for CrossPCAI. "
            "You are concise, technical and direct. You can dispatch tasks to "
            "the Hermes daemon, run commands in the sandbox, and report on "
            "agent and service health. Prefer action over explanation."
        ),
        "max_history": 30,
    },
    "opencode": {
        # OpenCode is the coding agent JARVAS hands real work to. Left mostly
        # empty so it uses whatever the machine already has configured.
        "binary": "",
        "model": "",
        "agent": "",
        "directory": "",
        "server_url": "http://127.0.0.1:4096",
        # --auto lets OpenCode approve its own shell commands and file writes.
        # Off unless the customer deliberately turns it on.
        "auto_approve": False,
        "timeout": 900,
    },
    "slack": {
        "enabled": False,
        "bot_token": "",          # xoxb-...
        "app_token": "",          # xapp-... (optional, socket mode)
        "default_channel": "",    # e.g. C0123456789 or #ai-ops-log
        "poll_seconds": 20,
        "notify_channels": [],
    },
    "agents": {
        # Registry of named agents JARVAS can dispatch to via Hermes.
        # Mirrors the 9-agent CrossPCAI workforce.
        "registry": [
            {"id": "lead-gen", "name": "Lead Generation", "kind": "hermes"},
            {"id": "sales", "name": "Sales Outreach", "kind": "hermes"},
            {"id": "crm", "name": "CRM Automation", "kind": "hermes"},
            {"id": "marketplace", "name": "Marketplace Distribution", "kind": "hermes"},
            {"id": "onboarding", "name": "POS Merchant Onboarding", "kind": "hermes"},
            {"id": "supplier", "name": "Compute Supplier Acquisition", "kind": "hermes"},
            {"id": "support", "name": "Customer Support", "kind": "hermes"},
            {"id": "publishing", "name": "Update Announcement", "kind": "hermes"},
            {"id": "ops", "name": "Daily Ops & Revenue", "kind": "hermes"},
        ]
    },
    "ui": {"theme": "dark", "start_minimized": False, "launch_on_login": False},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except (OSError, NotImplementedError):
        pass  # Windows / filesystems without POSIX modes
    migrate_legacy()


def migrate_legacy() -> list[str]:
    """Carry a pre-rename install's data over to the new filenames.

    Only moves a file when the new name does not already exist, so this can run
    on every start and can never overwrite current data with something stale.
    SQLite's -wal and -shm side files come with the database.
    """
    moved = []
    for old_name, new_path in _LEGACY:
        old_path = CONFIG_DIR / old_name
        if not old_path.exists() or new_path.exists():
            continue
        try:
            old_path.replace(new_path)
            moved.append(new_path.name)
            for suffix in ("-wal", "-shm"):
                side = CONFIG_DIR / (old_name + suffix)
                if side.exists():
                    side.replace(CONFIG_DIR / (new_path.name + suffix))
        except OSError:
            pass  # a locked file is not worth failing startup over
    return moved


def load_token() -> str:
    """Shared bearer token — created once, reused by every CrossPCAI service."""
    ensure_dirs()
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except (OSError, NotImplementedError):
        pass
    return tok


def load() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg = _deep_merge(cfg, json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # corrupt config must never block startup
    # Env overrides win over the file.
    if os.environ.get("SLACK_BOT_TOKEN"):
        cfg["slack"]["bot_token"] = os.environ["SLACK_BOT_TOKEN"]
        cfg["slack"]["enabled"] = True
    if os.environ.get("ANTHROPIC_API_KEY"):
        cfg["chat"].setdefault("anthropic_key", os.environ["ANTHROPIC_API_KEY"])
    if os.environ.get("OPENAI_API_KEY"):
        cfg["chat"].setdefault("openai_key", os.environ["OPENAI_API_KEY"])
    return cfg


def save(cfg: dict) -> None:
    ensure_dirs()
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    try:
        CONFIG_FILE.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


def redacted(cfg: dict) -> dict:
    """Config safe to hand to the browser — secrets masked, never sent raw."""
    import copy

    out = copy.deepcopy(cfg)
    for section, keys in (
        ("slack", ("bot_token", "app_token")),
        ("chat", ("anthropic_key", "openai_key")),
    ):
        for k in keys:
            v = out.get(section, {}).get(k)
            if v:
                out[section][k] = v[:7] + "…" + v[-4:] if len(v) > 12 else "set"
    return out


def host_info() -> dict:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "version": VERSION,
    }
