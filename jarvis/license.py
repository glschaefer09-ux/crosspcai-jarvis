#!/usr/bin/env python3
"""
license.py — activation for customers who bought the product.

Two paths, both offline-tolerant:

  Offline key   A signed key the customer pastes in. Format:
                    JARVIS-<tier>-<expiry|0>-<seats>-<sig>
                where sig = HMAC-SHA256(secret, "tier|expiry|seats")[:16].
                Validated locally, so a machine with no internet still starts.

  Online        POST to the configured activation endpoint, which returns the
                same fields. Used when the customer types an order email or a
                purchase reference instead.

Design note: the signing secret shipped in a desktop binary is discoverable by
anyone who looks. This gates honest customers and keeps entitlements tidy - it
is not, and cannot be, copy protection. Treat the server-side check as the real
one for anything that costs money to serve.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import platform
import time
import uuid
from pathlib import Path

from . import config
from .connectors import base

LICENSE_FILE = config.CONFIG_DIR / "license.json"

# Replace at build time (packaging/build_*.py reads JARVIS_SIGNING_SECRET).
SIGNING_SECRET = b"crosspcai-jarvis-default-secret"

TIERS = {
    "trial": {"label": "Trial", "seats": 1, "days": 14,
              "features": ["chat", "sandbox", "agents"]},
    "basic": {"label": "Basic", "seats": 1,
              "features": ["chat", "sandbox", "agents", "slack"]},
    "plus": {"label": "Plus", "seats": 3,
             "features": ["chat", "sandbox", "agents", "slack", "connectors", "tools"]},
    "premium": {"label": "Premium", "seats": 10,
                "features": ["chat", "sandbox", "agents", "slack", "connectors",
                             "tools", "nodes", "priority-support"]},
}


def _sign(tier: str, expiry: int, seats: int) -> str:
    msg = f"{tier}|{expiry}|{seats}".encode()
    return hmac.new(SIGNING_SECRET, msg, hashlib.sha256).hexdigest()[:16]


def make_key(tier: str, expiry: int = 0, seats: int = 1) -> str:
    """Key generator - used by the vendor, and by the tests."""
    return f"JARVIS-{tier}-{expiry}-{seats}-{_sign(tier, expiry, seats)}"


def machine_id() -> str:
    """Stable per-machine fingerprint for seat counting. Deliberately coarse -
    hostname plus platform, hashed - so it is not a hardware identifier."""
    raw = f"{platform.node()}|{platform.system()}|{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def validate_key(key: str) -> dict:
    parts = (key or "").strip().split("-")
    if len(parts) != 5 or parts[0] != "JARVIS":
        return {"ok": False, "error": "That does not look like a JARVIS key."}
    _, tier, expiry_s, seats_s, sig = parts
    try:
        expiry, seats = int(expiry_s), int(seats_s)
    except ValueError:
        return {"ok": False, "error": "Malformed key."}
    if tier not in TIERS:
        return {"ok": False, "error": f"Unknown tier: {tier}"}
    if not hmac.compare_digest(sig, _sign(tier, expiry, seats)):
        return {"ok": False, "error": "This key failed its signature check."}
    if expiry and time.time() > expiry:
        return {"ok": False, "expired": True,
                "error": "This licence expired on "
                         + time.strftime('%Y-%m-%d', time.localtime(expiry))}
    return {"ok": True, "tier": tier, "expiry": expiry, "seats": seats,
            "features": TIERS[tier]["features"]}


# -- stored state -------------------------------------------------------------

def load() -> dict:
    if not LICENSE_FILE.exists():
        return {"activated": False, "tier": None}
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"activated": False, "tier": None}
    # Re-validate on every load: an expired licence must not stay activated
    # just because it was written to disk while valid.
    if data.get("key"):
        check = validate_key(data["key"])
        if not check.get("ok"):
            return {"activated": False, "tier": data.get("tier"),
                    "error": check.get("error"), "expired": check.get("expired", False)}
    return data


def save(data: dict) -> None:
    config.ensure_dirs()
    LICENSE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        LICENSE_FILE.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


def status() -> dict:
    lic = load()
    if not lic.get("activated"):
        trial = start_or_get_trial()
        return {**trial, "activated": False}
    tier = lic.get("tier", "basic")
    return {
        "activated": True,
        "tier": tier,
        "label": TIERS.get(tier, {}).get("label", tier),
        "seats": lic.get("seats", 1),
        "expiry": lic.get("expiry", 0),
        "features": lic.get("features", TIERS.get(tier, {}).get("features", [])),
        "machine_id": machine_id(),
    }


def has_feature(name: str) -> bool:
    st = status()
    return name in (st.get("features") or [])


def activate(key: str) -> dict:
    check = validate_key(key)
    if not check.get("ok"):
        return check
    save({
        "activated": True, "key": key.strip(), "tier": check["tier"],
        "expiry": check["expiry"], "seats": check["seats"],
        "features": check["features"], "machine_id": machine_id(),
        "activated_at": time.time(),
    })
    return {"ok": True, **status()}


def activate_online(endpoint: str, reference: str) -> dict:
    """Exchange an order reference for a key at the vendor endpoint."""
    if not endpoint:
        return {"ok": False, "error": "No activation server is configured."}
    try:
        d = base.post(endpoint, json_body={
            "reference": reference, "machine_id": machine_id(),
            "app_version": config.VERSION, "platform": platform.system(),
        }, timeout=20) or {}
    except base.HttpError as e:
        return {"ok": False, "error": f"Activation server unreachable: {e}"}
    if not d.get("key"):
        return {"ok": False, "error": d.get("error", "No key was issued for that reference.")}
    return activate(d["key"])


def deactivate() -> dict:
    try:
        LICENSE_FILE.unlink(missing_ok=True)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


# -- trial --------------------------------------------------------------------

TRIAL_FILE = config.CONFIG_DIR / "trial.json"


def start_or_get_trial() -> dict:
    """Every fresh install gets a working trial, so the first launch does the
    job rather than showing a paywall."""
    config.ensure_dirs()
    if TRIAL_FILE.exists():
        try:
            t = json.loads(TRIAL_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            t = {}
    else:
        t = {}
    if not t.get("started_at"):
        t = {"started_at": time.time(), "id": uuid.uuid4().hex[:12]}
        try:
            TRIAL_FILE.write_text(json.dumps(t), encoding="utf-8")
        except OSError:
            pass
    days = TIERS["trial"]["days"]
    ends = t["started_at"] + days * 86400
    left = max(0, int((ends - time.time()) // 86400))
    return {
        "tier": "trial", "label": "Trial", "trial": True,
        "days_left": left, "expired": time.time() > ends,
        "features": TIERS["trial"]["features"],
        "seats": 1, "machine_id": machine_id(),
    }
