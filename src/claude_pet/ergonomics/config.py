"""User-editable ergonomics config at ~/.claude/claude-pet/config.json.

Defaults are the AOA/HSE/OSHA numbers. The user can turn categories off
individually, adjust intervals in minutes, set quiet hours, mute sounds,
or disable the coach entirely.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import scheduler


DEFAULTS = {
    "enabled": True,
    "sound": True,
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "intervals_min": {
        "eyes":      20,
        "neck":      30,
        "wrists":    45,
        "posture":   60,
        "hydration": 60,
    },
    "categories_enabled": {
        "eyes":      True,
        "neck":      True,
        "wrists":    True,
        "posture":   True,
        "hydration": True,
    },
}


def _config_path() -> Path:
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "config.json"


def load() -> dict:
    p = _config_path()
    if not p.exists():
        return dict(DEFAULTS)
    try:
        loaded = json.loads(p.read_text())
    except Exception:
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def save(cfg: dict) -> None:
    p = _config_path()
    p.write_text(json.dumps(cfg, indent=2))


def snoozed_until_ts(cfg: dict | None = None) -> float:
    """Wall-clock timestamp before which we must not prompt (0 if none).
    Written by `claude-pet ergonomics snooze N`; read by the pet's scheduler."""
    cfg = cfg or load()
    try:
        return float(cfg.get("_snoozed_until", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def effective_thresholds(cfg: dict | None = None) -> dict:
    """Return per-category thresholds in seconds, honoring category toggles.
    Disabled categories become 0 (which scheduler.check_due treats as skip)."""
    cfg = cfg or load()
    enabled = cfg.get("categories_enabled", {})
    minutes = cfg.get("intervals_min", DEFAULTS["intervals_min"])
    out = {}
    for cat, default_min in DEFAULTS["intervals_min"].items():
        if not enabled.get(cat, True):
            out[cat] = 0
            continue
        try:
            m = int(minutes.get(cat, default_min))
        except (TypeError, ValueError):
            m = default_min
        out[cat] = max(60, m * 60)         # floor at 1 min to prevent spam
    return out


def is_quiet_hours(cfg: dict | None = None, now=None) -> bool:
    """True if the current time is inside the user's configured quiet hours."""
    import datetime
    cfg = cfg or load()
    qh = cfg.get("quiet_hours", {})
    if not qh.get("enabled"):
        return False
    try:
        start_h, start_m = [int(x) for x in qh.get("start", "22:00").split(":")]
        end_h, end_m = [int(x) for x in qh.get("end", "07:00").split(":")]
    except Exception:
        return False
    now = now or datetime.datetime.now().time()
    start = datetime.time(start_h, start_m)
    end = datetime.time(end_h, end_m)
    if start <= end:
        return start <= now < end
    # Overnight (e.g. 22:00 → 07:00) — wraps midnight.
    return now >= start or now < end
