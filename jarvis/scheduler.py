#!/usr/bin/env python3
"""
scheduler.py — makes an agent's schedule actually fire.

The agent editor has always accepted a schedule. Until this module existed
nothing read it: a customer typed "daily 09:00", saved, and the agent never
ran. A silently ignored setting is worse than a missing one, so schedules are
parsed strictly and anything unrecognised is reported back to the UI rather
than being quietly dropped.

Accepted forms (case-insensitive):

    every 15m / every 2h        fixed interval
    hourly                      = every 1h
    daily 09:00                 once a day at that local time
    weekdays 09:00              Monday to Friday only
    weekly mon 09:00            one named day per week
    manual / (blank)            never fires on its own

Due-ness is computed from the agent's own last_run, so a machine that was
asleep at 09:00 runs the job when it wakes rather than skipping the day.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta

from . import agents, store

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_INTERVAL = re.compile(r"^every\s+(\d+)\s*([mh])$", re.I)
_DAILY = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.I)
_WEEKDAYS = re.compile(r"^weekdays\s+(\d{1,2}):(\d{2})$", re.I)
_WEEKLY = re.compile(r"^weekly\s+([a-z]{3})\s+(\d{1,2}):(\d{2})$", re.I)

# What a scheduled run asks the agent to do when the customer gave no input.
DEFAULT_BRIEF = "Scheduled run: carry out your standing brief and report what you found."


class BadSchedule(ValueError):
    pass


def parse(text: str) -> dict | None:
    """Return a normalised schedule, None for manual, or raise BadSchedule."""
    s = (text or "").strip().lower()
    if not s or s in ("manual", "none", "off"):
        return None
    if s == "hourly":
        return {"kind": "interval", "seconds": 3600, "label": "every hour"}
    if s == "daily":
        raise BadSchedule("Add a time, for example: daily 09:00")

    m = _INTERVAL.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if n <= 0:
            raise BadSchedule("The interval has to be more than zero.")
        secs = n * (60 if unit == "m" else 3600)
        if secs < 300:
            raise BadSchedule("The shortest interval is 5 minutes.")
        return {"kind": "interval", "seconds": secs,
                "label": f"every {n}{unit}"}

    for rx, kind in ((_DAILY, "daily"), (_WEEKDAYS, "weekdays")):
        m = rx.match(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            _check_time(hh, mm)
            return {"kind": kind, "hour": hh, "minute": mm,
                    "label": f"{kind} at {hh:02d}:{mm:02d}"}

    m = _WEEKLY.match(s)
    if m:
        day = m.group(1).lower()
        if day not in DAYS:
            raise BadSchedule(f"{day!r} is not a day. Use mon, tue, wed, thu, fri, sat or sun.")
        hh, mm = int(m.group(2)), int(m.group(3))
        _check_time(hh, mm)
        return {"kind": "weekly", "weekday": DAYS[day], "hour": hh, "minute": mm,
                "label": f"every {day} at {hh:02d}:{mm:02d}"}

    raise BadSchedule(
        "Try one of: 'every 30m', 'hourly', 'daily 09:00', "
        "'weekdays 09:00', or 'weekly mon 09:00'."
    )


def _check_time(hh: int, mm: int) -> None:
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise BadSchedule("That is not a valid time — use 24-hour HH:MM.")


def describe(text: str) -> str:
    """Human label for the UI, or the reason it will not run."""
    try:
        spec = parse(text)
    except BadSchedule as e:
        return f"not scheduled — {e}"
    return spec["label"] if spec else "manual"


def next_due(spec: dict, last_run: float | None, now: float | None = None) -> float:
    """When this schedule should next fire, as a unix timestamp."""
    now = now if now is not None else time.time()

    if spec["kind"] == "interval":
        # No history: fire one interval from now, not immediately - saving an
        # agent should not kick off a run the customer did not ask for.
        return (last_run or now) + spec["seconds"]

    base = datetime.fromtimestamp(last_run or now)
    at = datetime.fromtimestamp(now).replace(
        hour=spec["hour"], minute=spec["minute"], second=0, microsecond=0)

    if spec["kind"] == "daily":
        candidate = at if at.timestamp() > (last_run or 0) else at + timedelta(days=1)
        if candidate.timestamp() <= (last_run or 0):
            candidate += timedelta(days=1)
        return candidate.timestamp()

    if spec["kind"] == "weekdays":
        candidate = at
        if candidate.timestamp() <= (last_run or 0):
            candidate += timedelta(days=1)
        while candidate.weekday() > 4:  # skip Saturday and Sunday
            candidate += timedelta(days=1)
        return candidate.timestamp()

    if spec["kind"] == "weekly":
        candidate = at
        ahead = (spec["weekday"] - candidate.weekday()) % 7
        candidate += timedelta(days=ahead)
        if candidate.timestamp() <= (last_run or 0):
            candidate += timedelta(days=7)
        return candidate.timestamp()

    return now + 3600  # unreachable in practice; never busy-loop


def due_agents(now: float | None = None) -> list[dict]:
    """Enabled agents with a valid schedule whose next run has arrived."""
    now = now if now is not None else time.time()
    out = []
    for a in agents.list_agents():
        if not a.get("enabled") or not a.get("schedule"):
            continue
        try:
            spec = parse(a["schedule"])
        except BadSchedule:
            continue  # surfaced in the UI via describe(); never fire a guess
        if not spec:
            continue
        if next_due(spec, a.get("last_run"), now) <= now:
            out.append(a)
    return out


class Scheduler:
    """Background thread that runs due agents."""

    def __init__(self, app, interval: float = 30.0):
        self.app = app
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="jarvis-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - a bad agent must not kill the loop
                store.log_event("scheduler", f"Tick failed: {e}", level="error")

    def tick(self) -> list[str]:
        """Fire everything due. Returns the ids that were started."""
        fired = []
        for a in due_agents():
            res = agents.run(a["id"], DEFAULT_BRIEF, self.app)
            if res.get("ok"):
                fired.append(a["id"])
                store.log_event("scheduler",
                                f"{a['name']} ran on schedule ({a['schedule']})",
                                meta={"agent": a["id"], "run": res.get("run_id")})
            else:
                store.log_event("scheduler",
                                f"{a['name']} was due but could not start: "
                                f"{res.get('error')}", level="error",
                                meta={"agent": a["id"]})
        return fired


def status(app=None) -> dict:
    """What the Agents pane shows about scheduling."""
    rows = []
    for a in agents.list_agents():
        if not a.get("schedule"):
            continue
        try:
            spec = parse(a["schedule"])
            valid, label = True, (spec["label"] if spec else "manual")
            nxt = next_due(spec, a.get("last_run")) if spec else None
        except BadSchedule as e:
            valid, label, nxt = False, str(e), None
        rows.append({
            "id": a["id"], "name": a["name"], "schedule": a["schedule"],
            "valid": valid, "label": label, "enabled": a.get("enabled", True),
            "next_run": nxt, "last_run": a.get("last_run"),
        })
    return {"scheduled": rows, "count": len(rows)}
