#!/usr/bin/env python3
"""
telemetry.py — the product-signal channel back to CrossPCAI.

Purpose: when a customer creates an agent, wires a custom connector, or
requests one that doesn't exist yet, that is the clearest possible statement
of what the product is missing. Those signals flow to the CrossPCAI Notion
workspace so the agent workforce can build the connector or upgrade the tool.

Rules this module enforces, deliberately:
  * OFF until the customer turns it on in setup. No silent first-run send.
  * Only product signals. Never chat content, file contents, command output,
    Slack messages, tokens or keys — see SAFE_KEYS.
  * Queued to disk and retried, so a laptop offline on a plane still reports.
  * `preview()` returns exactly what would be sent, for the privacy pane.

Two transports: a generic ingest webhook (point it at n8n, which already
fronts Notion in the CrossPCAI stack) or the Notion API directly.
"""

from __future__ import annotations

import json
import platform
import threading
import time
import uuid
from pathlib import Path

from . import config
from .connectors import base

QUEUE_FILE = config.CONFIG_DIR / "telemetry_queue.jsonl"
INSTALL_FILE = config.CONFIG_DIR / "install_id"
MAX_QUEUE = 500

# Fields allowed to leave the machine. Anything not listed is dropped before
# the payload is built — allowlist, not blocklist, so a future caller can't
# leak by accident.
SAFE_KEYS = {
    "name", "role", "kind", "connectors", "connector", "category", "vendor",
    "auth_type", "base_url_host", "prompt_length", "has_schedule", "count",
    "reason", "detail", "version", "platform", "feature", "error_class",
    "tier", "seats", "requested", "fields",
}

_lock = threading.Lock()
_flusher: threading.Thread | None = None
_cfg: dict = {}


def _install_id() -> str:
    """Stable anonymous id for one installation. Not a user identifier."""
    config.ensure_dirs()
    if INSTALL_FILE.exists():
        v = INSTALL_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    v = uuid.uuid4().hex
    INSTALL_FILE.write_text(v + "\n", encoding="utf-8")
    return v


def configure(cfg: dict) -> None:
    global _cfg
    _cfg = cfg or {}


def enabled() -> bool:
    return bool(_cfg.get("enabled")) and bool(
        _cfg.get("ingest_url") or _cfg.get("notion_token")
    )


def _scrub(data: dict) -> dict:
    """Keep only allowlisted keys, and never let a value carry a secret."""
    out = {}
    for k, v in (data or {}).items():
        if k not in SAFE_KEYS:
            continue
        if isinstance(v, str):
            if len(v) > 500:
                v = v[:500] + "…"
            low = v.lower()
            if any(s in low for s in ("xoxb-", "xapp-", "sk-", "bearer ", "api_key")):
                continue  # looks like a credential — drop the field entirely
        out[k] = v
    return out


def report(event: str, data: dict | None = None) -> None:
    """Queue a product signal. Cheap and non-blocking; safe to call anywhere."""
    record = {
        "event": event,
        "data": _scrub(data or {}),
        "install_id": _install_id(),
        "app_version": config.VERSION,
        "platform": platform.system(),
        "at": time.time(),
    }
    if _cfg.get("customer_ref"):
        record["customer_ref"] = _cfg["customer_ref"]

    with _lock:
        try:
            config.ensure_dirs()
            lines = []
            if QUEUE_FILE.exists():
                lines = QUEUE_FILE.read_text(encoding="utf-8").splitlines()[-MAX_QUEUE:]
            lines.append(json.dumps(record))
            QUEUE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            return  # telemetry must never break the app
    if enabled():
        begin_flusher()


def preview(limit: int = 20) -> list[dict]:
    """Exactly what is queued to be sent — rendered in the privacy pane."""
    if not QUEUE_FILE.exists():
        return []
    out = []
    for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def clear() -> None:
    with _lock:
        try:
            QUEUE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ── delivery ─────────────────────────────────────────────────────────────────

def _send_ingest(records: list[dict]) -> bool:
    url = _cfg.get("ingest_url", "")
    if not url:
        return False
    try:
        base.post(url, json_body={"source": "jarvas", "records": records},
                  token=_cfg.get("ingest_token") or None, timeout=20)
        return True
    except base.HttpError:
        return False


def _send_notion(records: list[dict]) -> bool:
    """Create one page per signal in the configured Notion database."""
    token = _cfg.get("notion_token", "")
    db = _cfg.get("notion_database_id", "")
    if not (token and db):
        return False
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    ok_all = True
    for r in records:
        title = f"{r['event']} — {r['data'].get('name') or r['data'].get('connector') or ''}"
        payload = {
            "parent": {"database_id": db},
            "properties": {
                "Name": {"title": [{"text": {"content": title[:180]}}]},
            },
            "children": [{
                "object": "block", "type": "code",
                "code": {
                    "language": "json",
                    "rich_text": [{"type": "text",
                                   "text": {"content": json.dumps(r, indent=2)[:1900]}}],
                },
            }],
        }
        try:
            base.post("https://api.notion.com/v1/pages", json_body=payload,
                      headers=headers, timeout=20)
        except base.HttpError:
            ok_all = False
    return ok_all


def flush() -> dict:
    if not enabled():
        return {"ok": False, "reason": "telemetry disabled"}
    with _lock:
        if not QUEUE_FILE.exists():
            return {"ok": True, "sent": 0}
        lines = QUEUE_FILE.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not records:
        return {"ok": True, "sent": 0}

    sent = _send_ingest(records) or _send_notion(records)
    if sent:
        with _lock:
            try:
                QUEUE_FILE.unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": True, "sent": len(records)}
    return {"ok": False, "queued": len(records), "reason": "delivery failed, will retry"}


def begin_flusher(interval: float = 300.0) -> None:
    global _flusher
    if _flusher and _flusher.is_alive():
        return

    def loop():
        while True:
            time.sleep(interval)
            if enabled():
                flush()

    _flusher = threading.Thread(target=loop, daemon=True, name="jarvas-telemetry")
    _flusher.start()
