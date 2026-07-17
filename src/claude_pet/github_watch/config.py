"""GitHub watcher config — shares ~/.claude/claude-pet/config.json with ergonomics.

Uses the top-level `github` key so it never collides with ergonomics settings.
Never write GitHub tokens anywhere else.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULTS = {
    "enabled": True,
    "poll_interval_s": 300,
    "token": None,
    "alert_types": {
        "PushEvent":              True,
        "PullRequestEvent":       True,
        "PullRequestReviewEvent": True,
        "ReleaseEvent":           True,
        "IssuesEvent":            True,
        "WorkflowRunEvent":       True,
        "DeploymentStatusEvent":  True,
    },
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
    # tighten perms on POSIX so a stored PAT isn't world-readable
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def load() -> dict:
    """Return the merged `github` block with defaults applied."""
    raw = _load_all().get("github") or {}
    # Deep-copy defaults so callers can mutate nested dicts (e.g. alert_types)
    # without contaminating the module-level DEFAULTS.
    merged = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def save(cfg: dict) -> None:
    """Persist the merged block back under the `github` key. Leaves other keys alone."""
    all_cfg = _load_all()
    all_cfg["github"] = cfg
    _save_all(all_cfg)


def set_token(token: str | None) -> None:
    cfg = load()
    cfg["token"] = token or None
    save(cfg)


def token() -> str | None:
    """Prefer env override (CI, one-off) over stored config."""
    env = os.environ.get("CLAUDE_PET_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if env:
        return env.strip() or None
    t = load().get("token")
    return t if isinstance(t, str) and t.strip() else None


def poll_interval_s() -> int:
    try:
        v = int(load().get("poll_interval_s", 300))
    except (TypeError, ValueError):
        v = 300
    return max(60, v)      # floor at 1 minute to protect the rate limit


def enabled() -> bool:
    return bool(load().get("enabled", True))


def alert_type_enabled(event_type: str) -> bool:
    return bool(load().get("alert_types", {}).get(event_type, False))
