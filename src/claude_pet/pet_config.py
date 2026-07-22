"""Pet-wide user preferences (audio, quiet hours, etc.).

Kept in `~/.claude/claude-pet/config.json` under the top-level `pet` key
so it never collides with ergonomics (top-level keys) or github_watch
(top-level `github` key). Live-editable — every reader calls load()
fresh so a UI toggle takes effect on the next play() call, no restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULTS = {
    "muted": False,   # single boolean; True = every sound.play() no-ops
}


def _config_path() -> Path:
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "config.json"


def _load_all() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_all(all_cfg: dict) -> None:
    p = _config_path()
    p.write_text(json.dumps(all_cfg, indent=2))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def load() -> dict:
    raw = _load_all().get("pet") or {}
    merged = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def save(cfg: dict) -> None:
    all_cfg = _load_all()
    all_cfg["pet"] = cfg
    _save_all(all_cfg)


def is_muted() -> bool:
    """Fresh read every call — a UI toggle takes effect immediately."""
    return bool(load().get("muted", False))


def set_muted(muted: bool) -> None:
    cfg = load()
    cfg["muted"] = bool(muted)
    save(cfg)


def toggle_muted() -> bool:
    """Flip and return the new state."""
    new_val = not is_muted()
    set_muted(new_val)
    return new_val
