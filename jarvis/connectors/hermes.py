#!/usr/bin/env python3
"""
hermes.py — client for the Hermes automation daemon (hermes_agent.py, :5562).

Endpoints it exposes:  POST /task   GET /tasks   GET /status
"""

from __future__ import annotations

from . import base


class Hermes:
    def __init__(self, host: str, port: int, token: str):
        self.base_url = f"http://{host}:{port}"
        self.host, self.port, self.token = host, port, token

    def alive(self) -> bool:
        return base.port_open(self.host, self.port)

    def status(self) -> dict:
        try:
            return {"ok": True, **(base.get(f"{self.base_url}/status", token=self.token) or {})}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}

    def tasks(self) -> list[dict]:
        try:
            data = base.get(f"{self.base_url}/tasks", token=self.token) or {}
        except base.HttpError:
            return []
        if isinstance(data, list):
            return data
        # hermes_agent returns {"queue": [...], "history": [...]}
        return list(data.get("queue", [])) + list(data.get("history", []))

    def dispatch(self, description: str, priority: str = "normal",
                 agent_id: str | None = None) -> dict:
        """Queue a task. agent_id is prefixed so Hermes history stays readable."""
        desc = f"[{agent_id}] {description}" if agent_id else description
        try:
            res = base.post(
                f"{self.base_url}/task",
                token=self.token,
                json_body={"description": desc, "priority": priority},
            )
            return {"ok": True, **(res or {})}
        except base.HttpError as e:
            return {"ok": False, "error": str(e)}
