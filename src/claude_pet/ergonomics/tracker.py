"""Activity tracker — counts only REAL work time from Claude Code events.

The scheduler thresholds (20 min eye break, 30 min microbreak, 60 min hourly)
must never be triggered by wall-clock time while the user is at lunch.
Every method here answers one of:
  - "did the user just do something?"  → mark_activity()
  - "is the user idle right now?"      → mark_idle()
  - "how long has this counted, TOTAL, since a category was last completed?"

State persists in memory.sqlite so restarts and pet upgrades don't reset the
running counters. We store the current running-window START rather than a
running total, because a total requires flushing on every event — expensive
under high tool-call rates. Storing (window_started_at, is_active_now) lets
us compute active seconds on demand and only write on state transitions.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable

from .. import memory


# --------------------------------------------------------------------- schema
# Reuses the existing sqlite file; additive-only. Called on demand.
_ERGO_SCHEMA = """
CREATE TABLE IF NOT EXISTS ergo_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ergo_breaks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,                -- ISO8601 UTC
  category TEXT NOT NULL,
  exercise TEXT NOT NULL,
  completed INTEGER NOT NULL       -- 0 skipped, 1 done
);

CREATE INDEX IF NOT EXISTS idx_ergo_breaks_ts ON ergo_breaks(ts);
"""


def _ensure_schema(conn) -> None:
    conn.executescript(_ERGO_SCHEMA)


def _now_s() -> float:
    """Monotonic-safe seconds since epoch. We use time.time() rather than
    time.monotonic() because we persist across process restarts."""
    return time.time()


def _get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM ergo_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _put(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO ergo_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --------------------------------------------------------------------- API

def mark_activity(now: float | None = None) -> None:
    """Called on every real user activity event (UserPromptSubmit, PostToolUse).
    If a window isn't running, start one. If one is running, extend nothing —
    the window keeps accumulating until mark_idle() is called."""
    now = now if now is not None else _now_s()
    with memory.connect() as conn:
        _ensure_schema(conn)
        active = _get(conn, "active_now", "0") == "1"
        if not active:
            # Starting a new window — remember when, and clear any per-category
            # exclusions from the previous window.
            _put(conn, "active_now", "1")
            _put(conn, "window_started_at", str(now))
            for cat in ("eyes", "neck", "wrists", "posture", "hydration"):
                _put(conn, f"window_excluded_{cat}", "0")
        _put(conn, "last_activity_at", str(now))


def mark_idle(now: float | None = None) -> None:
    """Called when the user has clearly stopped working (existing 3-min sleep
    state, explicit snooze, panel opened for a long look, etc.). Freezes the
    active-second counter until the next mark_activity()."""
    now = now if now is not None else _now_s()
    with memory.connect() as conn:
        _ensure_schema(conn)
        active = _get(conn, "active_now", "0") == "1"
        if not active:
            return  # already idle
        started = float(_get(conn, "window_started_at", str(now)))
        # Bank the window's elapsed active seconds into per-category counters.
        elapsed = max(0.0, now - started)
        _bank_seconds(conn, elapsed)
        _put(conn, "active_now", "0")


def _bank_seconds(conn, seconds: float) -> None:
    """Add `seconds` to every category-specific running total, minus any
    portion of the current window that was already credited to that category
    (e.g. a break completed mid-window shouldn't double-count)."""
    for cat in ("eyes", "neck", "wrists", "posture", "hydration"):
        excluded = float(_get(conn, f"window_excluded_{cat}", "0"))
        credited = max(0.0, seconds - excluded)
        key = f"active_since_last_{cat}"
        current = float(_get(conn, key, "0"))
        _put(conn, key, str(current + credited))
        # Window is ending — the exclusion offset only applied to it.
        _put(conn, f"window_excluded_{cat}", "0")


def active_seconds_since_last(category: str) -> float:
    """How many real work-seconds have elapsed since the last completed break
    in this category. Includes any in-progress work window minus whatever
    portion was already credited via a mid-window completion."""
    with memory.connect() as conn:
        _ensure_schema(conn)
        banked = float(_get(conn, f"active_since_last_{category}", "0"))
        active = _get(conn, "active_now", "0") == "1"
        if active:
            started = float(_get(conn, "window_started_at", str(_now_s())))
            excluded = float(_get(conn, f"window_excluded_{category}", "0"))
            current_window = max(0.0, _now_s() - started)
            banked += max(0.0, current_window - excluded)
        return banked


def note_break_completed(category: str, exercise_slug: str,
                         completed: bool = True, now: float | None = None) -> None:
    """Log the break and reset the category counter."""
    now = now if now is not None else _now_s()
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    with memory.connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO ergo_breaks (ts, category, exercise, completed) "
            "VALUES (?, ?, ?, ?)",
            (now_dt.isoformat(timespec="seconds"), category, exercise_slug,
             1 if completed else 0),
        )
        # Only completed breaks reset — skipping means the body still owes.
        if not completed:
            return
        _put(conn, f"active_since_last_{category}", "0")
        # If a window is running, freeze the elapsed portion so the currently-
        # open window doesn't re-credit this category over and over.
        active = _get(conn, "active_now", "0") == "1"
        if active:
            started = float(_get(conn, "window_started_at", str(now)))
            _put(conn, f"window_excluded_{category}",
                 str(max(0.0, now - started)))


# --------------------------------------------------------------------- stats

def today_breaks() -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    with memory.connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT ts, category, exercise, completed FROM ergo_breaks "
            "WHERE ts >= ? ORDER BY ts DESC",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


def adherence_last_n_days(n: int = 7) -> dict:
    """Ratio of completed / total prompted breaks over the last N days."""
    with memory.connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT "
            "  COUNT(*) AS total, "
            "  COALESCE(SUM(completed), 0) AS done "
            "FROM ergo_breaks "
            "WHERE ts >= date('now', ?)",
            (f"-{n} days",),
        ).fetchone()
    total = row["total"] or 0
    done = row["done"] or 0
    return {
        "total": total,
        "completed": done,
        "adherence": (done / total) if total else 0.0,
        "days": n,
    }


def daily_streak() -> int:
    """Consecutive days ending today with at least one completed break."""
    with memory.connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT DISTINCT date(ts) AS d FROM ergo_breaks "
            "WHERE completed=1 ORDER BY d DESC"
        ).fetchall()
    dates = [r["d"] for r in rows]
    if not dates:
        return 0
    today = datetime.now(timezone.utc).date()
    streak = 0
    for offset in range(len(dates) + 1):
        expected = today.isoformat() if offset == 0 else \
            (today.fromordinal(today.toordinal() - offset)).isoformat()
        if expected in dates:
            streak += 1
        else:
            break
    return streak


def most_skipped_exercise() -> tuple[str, int] | None:
    with memory.connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT exercise, COUNT(*) AS n FROM ergo_breaks "
            "WHERE completed=0 "
            "GROUP BY exercise ORDER BY n DESC LIMIT 1"
        ).fetchone()
    if not row or not row["n"]:
        return None
    return (row["exercise"], row["n"])


def reset_all() -> None:
    """Wipes ergonomics state — for tests and `claude-pet ergonomics reset`."""
    with memory.connect() as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM ergo_state")
        conn.execute("DELETE FROM ergo_breaks")
