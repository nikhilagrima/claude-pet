"""User-editable fitness config at ~/.claude/claude-pet/fitness.json.

Kept in its own file (not shared with ergonomics / pet-wide config) so a
profile edit can't accidentally reset an unrelated setting. Same load/merge
pattern as the ergonomics module: DEFAULTS is deep-copied, user overrides
merged on top, missing keys back-filled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULTS = {
    "enabled": True,
    # User's body profile — override with real values via CLI or the
    # Settings tab (Settings tab wiring is a follow-up).
    "profile": {
        "weight_kg": 80.0,
        "height_cm": 175.0,
        "age": 30,
        "male": True,
        "target_weight_kg": 72.0,
        # Activity multiplier for TDEE — light (1.375) = desk job + 3-4
        # workouts/week. Moderate (1.55), very active (1.725).
        "activity_factor": 1.375,
    },
    # Reminder times — 24-hour HH:MM local. Empty string disables that
    # reminder entirely.
    "reminders": {
        "workout":    "07:00",
        "weigh_in":   "07:30",
        "meal_check": "20:30",
    },
    # When True, the pet's SessionStart hook will inject a weekly
    # adjustment request into Claude Code once Sunday reached and no
    # note for the current week exists (see coach.py).
    "agentic_coach": True,
    # Bookkeeping — filled in by coach.py; DON'T edit by hand.
    "_last_fired": {},        # {"workout": "2026-07-23", ...}
    "_note_shown_date": "",   # last date the fitness_note.txt was displayed
    "_week_note_generated": "",  # ISO week ID the coach note was generated for
}


def _config_path() -> Path:
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "fitness.json"


def _deep_default() -> dict:
    return {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in DEFAULTS.items()}


def load() -> dict:
    """Return merged config. Missing file → DEFAULTS. Merge is one level deep."""
    p = _config_path()
    merged = _deep_default()
    if not p.exists():
        return merged
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return merged
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def save(cfg: dict) -> None:
    p = _config_path()
    p.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(p, 0o600)     # profile is personal info
    except Exception:
        pass


def profile() -> dict:
    return load().get("profile", DEFAULTS["profile"])


def is_enabled() -> bool:
    return bool(load().get("enabled", True))


def agentic_coach_enabled() -> bool:
    return bool(load().get("agentic_coach", True))


def reminders() -> dict:
    return load().get("reminders", DEFAULTS["reminders"])


def _fitness_note_path() -> Path:
    return Path.home() / ".claude" / "claude-pet" / "fitness_note.txt"
