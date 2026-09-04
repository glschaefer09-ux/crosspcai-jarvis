#!/usr/bin/env python3
"""
slack.py — Slack Web API client (bot token) for the JARVIS Slack workspace pane.

Covers what the CrossPCAI ops loop needs: list channels, read a channel,
post a message, and poll for new activity to surface as JARVIS notifications.

Scopes the bot token needs:
    channels:read  channels:history  groups:read  groups:history
    chat:write     users:read        reactions:write
"""

from __future__ import annotations

import time

from . import base

API = "https://slack.com/api"


class SlackError(Exception):
    pass


class Slack:
    def __init__(self, token: str = "", default_channel: str = ""):
        self.token = (token or "").strip()
        self.default_channel = default_channel
        self._user_cache: dict[str, str] = {}
        self._channel_cache: list[dict] = []
        self._channel_cache_at = 0.0

    # ── plumbing ─────────────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        return self.token.startswith("xox")

    def _call(self, method: str, params: dict | None = None, post: bool = False) -> dict:
        if not self.configured:
            raise SlackError("Slack bot token not configured")
        url = f"{API}/{method}"
        if post:
            data = base.post(url, token=self.token, form=params or {})
        else:
            import urllib.parse
            qs = urllib.parse.urlencode(params or {})
            data = base.get(f"{url}?{qs}" if qs else url, token=self.token)
        if not isinstance(data, dict):
            raise SlackError(f"unexpected response from {method}")
        if not data.get("ok"):
            raise SlackError(data.get("error", "unknown_error"))
        return data

    # ── identity ─────────────────────────────────────────────────────────────

    def auth_test(self) -> dict:
        try:
            d = self._call("auth.test")
            return {"ok": True, "team": d.get("team"), "user": d.get("user"),
                    "user_id": d.get("user_id"), "team_id": d.get("team_id")}
        except (SlackError, base.HttpError) as e:
            return {"ok": False, "error": str(e)}

    def user_name(self, uid: str) -> str:
        if not uid:
            return "unknown"
        if uid in self._user_cache:
            return self._user_cache[uid]
        try:
            d = self._call("users.info", {"user": uid})
            u = d.get("user", {})
            name = u.get("profile", {}).get("display_name") or u.get("real_name") or uid
        except (SlackError, base.HttpError):
            name = uid
        self._user_cache[uid] = name
        return name

    # ── channels ─────────────────────────────────────────────────────────────

    def channels(self, force: bool = False) -> list[dict]:
        if not force and self._channel_cache and time.time() - self._channel_cache_at < 300:
            return self._channel_cache
        try:
            d = self._call("conversations.list", {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": "200",
            })
        except (SlackError, base.HttpError):
            return self._channel_cache
        chans = [
            {"id": c["id"], "name": c.get("name", ""),
             "is_member": c.get("is_member", False),
             "private": c.get("is_private", False)}
            for c in d.get("channels", [])
        ]
        chans.sort(key=lambda c: (not c["is_member"], c["name"]))
        self._channel_cache, self._channel_cache_at = chans, time.time()
        return chans

    def resolve_channel(self, ref: str) -> str:
        """Accept a channel id, #name or name and return the id."""
        ref = (ref or self.default_channel or "").strip()
        if not ref:
            raise SlackError("no channel specified")
        if ref.startswith(("C", "G", "D")) and " " not in ref and not ref.startswith("#"):
            return ref
        name = ref.lstrip("#")
        for c in self.channels():
            if c["name"] == name:
                return c["id"]
        raise SlackError(f"channel not found: {ref}")

    def history(self, channel: str, limit: int = 50, oldest: float = 0.0) -> list[dict]:
        cid = self.resolve_channel(channel)
        params = {"channel": cid, "limit": str(limit)}
        if oldest:
            params["oldest"] = f"{oldest:.6f}"
        try:
            d = self._call("conversations.history", params)
        except (SlackError, base.HttpError):
            return []
        msgs = []
        for m in reversed(d.get("messages", [])):
            if m.get("subtype") in ("channel_join", "channel_leave"):
                continue
            msgs.append({
                "ts": m.get("ts"),
                "time": float(m.get("ts", 0)),
                "user": self.user_name(m.get("user", "")) if m.get("user") else
                        m.get("username", "bot"),
                "user_id": m.get("user", ""),
                "text": m.get("text", ""),
                "thread_ts": m.get("thread_ts"),
                "reply_count": m.get("reply_count", 0),
            })
        return msgs

    def send(self, channel: str, text: str, thread_ts: str | None = None) -> dict:
        """Post a message. Only ever called from an explicit user action in the UI."""
        try:
            cid = self.resolve_channel(channel)
            params = {"channel": cid, "text": text}
            if thread_ts:
                params["thread_ts"] = thread_ts
            d = self._call("chat.postMessage", params, post=True)
            return {"ok": True, "ts": d.get("ts"), "channel": cid}
        except (SlackError, base.HttpError) as e:
            return {"ok": False, "error": str(e)}

    def react(self, channel: str, ts: str, emoji: str = "white_check_mark") -> dict:
        try:
            cid = self.resolve_channel(channel)
            self._call("reactions.add", {"channel": cid, "timestamp": ts, "name": emoji},
                       post=True)
            return {"ok": True}
        except (SlackError, base.HttpError) as e:
            return {"ok": False, "error": str(e)}
