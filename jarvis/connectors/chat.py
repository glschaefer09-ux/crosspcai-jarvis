#!/usr/bin/env python3
"""
chat.py — LLM provider abstraction for the JARVIS chat pane.

Providers: ollama (local, default — no key, ships with CrossPC AI OS),
           anthropic, openai. All three speak plain HTTP via base.request,
           so no vendor SDK has to survive PyInstaller freezing.

Also owns tool-calling: JARVIS can dispatch Hermes tasks, run sandbox
commands and post to Slack from inside a chat turn.
"""

from __future__ import annotations

import json
import re

from . import base

# Actions JARVIS may take from a chat turn. The model emits a fenced
# ```jarvis {...}``` block; we parse and execute it, then feed results back.
TOOL_SPEC = """
You can act on the machine by emitting a fenced block:

```jarvis
{"tool": "<name>", "args": { ... }}
```

Available tools:
  hermes.task    {"description": "...", "priority": "normal|high"}
  sandbox.exec   {"cmd": "..."}
  slack.send     {"channel": "#name", "text": "..."}
  status.check   {}
  opencode.run   {"prompt": "...", "directory": "/path"}   hand real coding
                 work to the OpenCode agent on this machine (it edits files
                 and runs commands; prefer it over sandbox.exec for anything
                 that changes a project)

Emit at most one tool block per reply. After the result comes back, continue.
If no tool is needed, just answer.
"""

# Small local models routinely ignore the fence and emit bare JSON, or use a
# ```json fence instead. Accept all three rather than leaking raw JSON into the
# chat window - the model being sloppy is not the customer's problem.
_FENCED_RE = re.compile(r"```(?:jarvis|json)?\s*(\{.*?\})\s*```", re.S)


def _valid(raw: str) -> dict | None:
    try:
        call = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return call if isinstance(call, dict) and call.get("tool") else None


def _find_bare(text: str) -> tuple[dict, int, int] | None:
    """Scan for a balanced {...} object carrying a "tool" key.

    A regex cannot do this - "args": {} nests - so walk the braces, skipping
    anything inside a JSON string so a brace in a command does not confuse us.
    """
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    call = _valid(text[start:i + 1])
                    if call:
                        return call, start, i + 1
                    break
    return None


def extract_tool_call(text: str) -> dict | None:
    for m in _FENCED_RE.finditer(text or ""):
        call = _valid(m.group(1))
        if call:
            return call
    found = _find_bare(text or "")
    return found[0] if found else None


def strip_tool_call(text: str) -> str:
    out = _FENCED_RE.sub("", text or "")
    found = _find_bare(out)
    if found:
        _, start, end = found
        out = out[:start] + out[end:]
    return out.strip()


class ChatProvider:
    """One object per configured provider; complete() is the only entry point."""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.provider = self.cfg.get("provider", "ollama")

    # ── public ───────────────────────────────────────────────────────────────

    def describe(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model_name(),
            "ready": self.ready(),
        }

    def model_name(self) -> str:
        return {
            "ollama": self.cfg.get("ollama_model", "llama3.2:latest"),
            "anthropic": self.cfg.get("anthropic_model", "claude-opus-5"),
            "openai": self.cfg.get("openai_model", "gpt-4o-mini"),
        }.get(self.provider, "unknown")

    def ready(self) -> bool:
        if self.provider == "ollama":
            url = self.cfg.get("ollama_url", "http://127.0.0.1:11434")
            host, _, port = url.split("://", 1)[1].partition(":")
            return base.port_open(host, int(port or 11434), timeout=0.5)
        if self.provider == "anthropic":
            return bool(self.cfg.get("anthropic_key"))
        if self.provider == "openai":
            return bool(self.cfg.get("openai_key"))
        return False

    def complete(self, messages: list[dict], system: str = "", tools: bool = True) -> dict:
        """messages = [{'role': 'user'|'assistant', 'content': str}]"""
        sys_prompt = (system or self.cfg.get("system_prompt", "")).strip()
        if tools:
            sys_prompt = f"{sys_prompt}\n\n{TOOL_SPEC}".strip()
        try:
            if self.provider == "anthropic":
                text = self._anthropic(messages, sys_prompt)
            elif self.provider == "openai":
                text = self._openai(messages, sys_prompt)
            else:
                text = self._ollama(messages, sys_prompt)
            return {"ok": True, "text": text, "tool_call": extract_tool_call(text)}
        except base.HttpError as e:
            return {"ok": False, "error": str(e), "text": f"[{self.provider} unreachable] {e}"}
        except Exception as e:  # provider quirks must not kill the chat pane
            return {"ok": False, "error": str(e), "text": f"[chat error] {e}"}

    # ── providers ────────────────────────────────────────────────────────────

    def _ollama(self, messages: list[dict], system: str) -> str:
        url = self.cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        payload = {
            "model": self.model_name(),
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
            "stream": False,
        }
        d = base.post(f"{url}/api/chat", json_body=payload, timeout=300) or {}
        return (d.get("message") or {}).get("content", "") or d.get("response", "")

    def _anthropic(self, messages: list[dict], system: str) -> str:
        key = self.cfg.get("anthropic_key", "")
        payload = {
            "model": self.model_name(),
            "max_tokens": 2048,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        d = base.post(
            "https://api.anthropic.com/v1/messages",
            json_body=payload,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=300,
        ) or {}
        parts = d.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")

    def _openai(self, messages: list[dict], system: str) -> str:
        key = self.cfg.get("openai_key", "")
        payload = {
            "model": self.model_name(),
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        }
        d = base.post(
            "https://api.openai.com/v1/chat/completions",
            json_body=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=300,
        ) or {}
        choices = d.get("choices", [])
        return choices[0]["message"]["content"] if choices else ""

    def available_models(self) -> list[str]:
        """Used by the setup wizard to populate the model dropdown."""
        if self.provider != "ollama":
            return [self.model_name()]
        url = self.cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
        try:
            d = base.get(f"{url}/api/tags", timeout=5) or {}
            return [m["name"] for m in d.get("models", []) if m.get("name")]
        except base.HttpError:
            return []
