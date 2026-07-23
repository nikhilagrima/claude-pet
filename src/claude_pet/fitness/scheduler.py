"""Scheduler — decides when to fire a fitness reminder.

Same shape as ergonomics.scheduler: pure function called from the pet's
existing Qt tick, no threads. Each reminder fires AT MOST ONCE PER DAY
at or after its configured HH:MM local time. State (`_last_fired` per
reminder) lives in fitness.json so restarts don't re-fire.

check_due(now) returns "workout" | "weigh_in" | "meal_check" | None.
Order of precedence when multiple are due at the same moment:
  weigh_in > workout > meal_check
(weigh yourself before you work out; end-of-day meal check goes last.)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from . import config as fcfg


_REMINDER_ORDER = ("weigh_in", "workout", "meal_check")


def _parse_hhmm(s: str) -> Optional[tuple[int, int]]:
    if not s:
        return None
    try:
        h, m = s.split(":", 1)
        return (int(h), int(m))
    except Exception:
        return None


def check_due(now: Optional[datetime] = None) -> Optional[str]:
    """Return the first reminder that's due right now, or None.

    Fires each reminder at most once per day. Reads config every call so a
    user-edited HH:MM takes effect on the next tick (no restart needed).
    """
    if not fcfg.is_enabled():
        return None
    now = now or datetime.now()
    today = date.today().isoformat()
    cfg = fcfg.load()
    last_fired = dict(cfg.get("_last_fired", {}))
    reminders = cfg.get("reminders", {})

    for name in _REMINDER_ORDER:
        hhmm = _parse_hhmm(reminders.get(name, ""))
        if hhmm is None:
            continue
        hour, minute = hhmm
        # Only fire once we're at or past the target time
        if (now.hour, now.minute) < (hour, minute):
            continue
        # Only fire once per day
        if last_fired.get(name) == today:
            continue
        return name
    return None


def mark_fired(name: str) -> None:
    """Persist that `name` fired today so we don't repeat it."""
    cfg = fcfg.load()
    last_fired = dict(cfg.get("_last_fired", {}))
    last_fired[name] = date.today().isoformat()
    cfg["_last_fired"] = last_fired
    fcfg.save(cfg)
