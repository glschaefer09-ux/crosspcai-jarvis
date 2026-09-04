#!/usr/bin/env python3
"""
store.py — SQLite persistence for chat sessions, messages and the event log.

Single connection guarded by a lock: JARVIS is a single-process app and the
write volume is tiny, so this is simpler and safer than a pool.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    meta       TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    text       TEXT NOT NULL,
    meta       TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(title: str = "New session", kind: str = "chat") -> dict:
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        c = connect()
        c.execute(
            "INSERT INTO sessions (id,title,kind,created_at,updated_at) VALUES (?,?,?,?,?)",
            (sid, title, kind, now, now),
        )
        c.commit()
    return {"id": sid, "title": title, "kind": kind, "created_at": now, "updated_at": now}


def list_sessions(limit: int = 100) -> list[dict]:
    with _lock:
        rows = connect().execute(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS n "
            "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def rename_session(sid: str, title: str) -> None:
    with _lock:
        c = connect()
        c.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, time.time(), sid))
        c.commit()


def delete_session(sid: str) -> None:
    with _lock:
        c = connect()
        c.execute("DELETE FROM sessions WHERE id=?", (sid,))
        c.commit()


def touch_session(sid: str) -> None:
    with _lock:
        c = connect()
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))
        c.commit()


# ── Messages ──────────────────────────────────────────────────────────────────

def add_message(sid: str, role: str, content: str, meta: dict | None = None) -> dict:
    now = time.time()
    with _lock:
        c = connect()
        cur = c.execute(
            "INSERT INTO messages (session_id,role,content,meta,created_at) VALUES (?,?,?,?,?)",
            (sid, role, content, json.dumps(meta) if meta else None, now),
        )
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
        c.commit()
        mid = cur.lastrowid
    return {"id": mid, "session_id": sid, "role": role, "content": content,
            "meta": meta, "created_at": now}


def get_messages(sid: str, limit: int = 200) -> list[dict]:
    with _lock:
        rows = connect().execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id LIMIT ?", (sid, limit)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d["meta"] else None
        out.append(d)
    return out


def history_for_model(sid: str, max_turns: int) -> list[dict[str, str]]:
    """Trimmed role/content pairs for the LLM — drops meta and system rows."""
    msgs = get_messages(sid)
    msgs = [m for m in msgs if m["role"] in ("user", "assistant")]
    return [{"role": m["role"], "content": m["content"]} for m in msgs[-max_turns:]]


# ── Events ────────────────────────────────────────────────────────────────────

def log_event(source: str, text: str, level: str = "info", meta: dict | None = None) -> None:
    with _lock:
        c = connect()
        c.execute(
            "INSERT INTO events (source,level,text,meta,created_at) VALUES (?,?,?,?,?)",
            (source, level, text, json.dumps(meta) if meta else None, time.time()),
        )
        c.commit()


def recent_events(limit: int = 100, since: float = 0.0) -> list[dict]:
    with _lock:
        rows = connect().execute(
            "SELECT * FROM events WHERE created_at > ? ORDER BY id DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d["meta"] else None
        out.append(d)
    return out
