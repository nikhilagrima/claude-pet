"""SQLite tracker at ~/.claude/claude-pet/fitness.db.

Kept in its own DB file (not the memory.sqlite one) so a memory-brain wipe or
a delete-project cascade never touches fitness history. Three tables, all
day-scoped, all `INSERT OR REPLACE` so the day's row overwrites cleanly if
you log twice.

Schema is created lazily on first connect; every read/write goes through
_ensure_schema so we never crash on a fresh install.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


def db_path() -> Path:
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "fitness.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS weight_log (
  day        TEXT PRIMARY KEY,          -- YYYY-MM-DD, one row per day
  weight_kg  REAL NOT NULL,
  logged_at  TEXT NOT NULL              -- ISO8601 UTC
);

CREATE TABLE IF NOT EXISTS workout_log (
  day        TEXT PRIMARY KEY,
  focus      TEXT NOT NULL,             -- PUSH / PULL / CARDIO / etc.
  completed  INTEGER NOT NULL,          -- 0 = skipped, 1 = done
  logged_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meal_log (
  day        TEXT PRIMARY KEY,
  on_plan    INTEGER NOT NULL,          -- 0 = off plan, 1 = on plan
  note       TEXT,
  logged_at  TEXT NOT NULL
);

-- Direct body-part completions from the clickable body map. Independent of
-- workout_log (which is focus-based) so users can tick off e.g. just
-- "biceps" today without claiming the entire PULL focus is done.
CREATE TABLE IF NOT EXISTS body_part_log (
  day        TEXT NOT NULL,
  part       TEXT NOT NULL,             -- lowercase name matching ALL_BODY_PARTS
  logged_at  TEXT NOT NULL,
  PRIMARY KEY (day, part)
);
"""


def _ensure_schema(conn) -> None:
    conn.executescript(_SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(str(db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _today() -> str:
    return date.today().isoformat()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- writes -----------------------------------------------------------------

def log_weight(weight_kg: float, day: Optional[str] = None) -> None:
    """One entry per day. Re-logging overwrites the day's row."""
    d = day or _today()
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO weight_log (day, weight_kg, logged_at) "
            "VALUES (?, ?, ?)",
            (d, float(weight_kg), _now_utc()),
        )


def log_workout(focus: str, completed: bool = True,
                 day: Optional[str] = None) -> None:
    d = day or _today()
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO workout_log (day, focus, completed, logged_at) "
            "VALUES (?, ?, ?, ?)",
            (d, focus.upper(), 1 if completed else 0, _now_utc()),
        )


def log_meal(on_plan: bool, note: str = "",
              day: Optional[str] = None) -> None:
    d = day or _today()
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO meal_log (day, on_plan, note, logged_at) "
            "VALUES (?, ?, ?, ?)",
            (d, 1 if on_plan else 0, note, _now_utc()),
        )


# --- reads ------------------------------------------------------------------

def recent(days: int = 14) -> dict:
    """Return the last N days of tracking, keyed by table name.

    Shape:
      {
        "weights":  [{"day": "2026-07-23", "weight_kg": 79.2}, ...],
        "workouts": [{"day": "...", "focus": "PUSH", "completed": True}, ...],
        "meals":    [{"day": "...", "on_plan": True, "note": ""}, ...],
      }

    Sorted newest-first. Missing days simply don't appear.
    """
    cutoff = (date.today() - timedelta(days=max(0, days - 1))).isoformat()
    with connect() as c:
        weights = [
            {"day": r["day"], "weight_kg": r["weight_kg"]}
            for r in c.execute(
                "SELECT day, weight_kg FROM weight_log WHERE day >= ? "
                "ORDER BY day DESC", (cutoff,),
            )
        ]
        workouts = [
            {"day": r["day"], "focus": r["focus"],
             "completed": bool(r["completed"])}
            for r in c.execute(
                "SELECT day, focus, completed FROM workout_log WHERE day >= ? "
                "ORDER BY day DESC", (cutoff,),
            )
        ]
        meals = [
            {"day": r["day"], "on_plan": bool(r["on_plan"]),
             "note": r["note"] or ""}
            for r in c.execute(
                "SELECT day, on_plan, note FROM meal_log WHERE day >= ? "
                "ORDER BY day DESC", (cutoff,),
            )
        ]
    return {"weights": weights, "workouts": workouts, "meals": meals}


def latest_weight() -> Optional[float]:
    with connect() as c:
        r = c.execute(
            "SELECT weight_kg FROM weight_log ORDER BY day DESC LIMIT 1"
        ).fetchone()
    return float(r["weight_kg"]) if r else None


# --- BODY-PART LOG (feeds the clickable body map) --------------------------

def log_body_part(part: str, day: Optional[str] = None) -> None:
    """Mark one specific body part as trained today (idempotent)."""
    d = day or _today()
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO body_part_log (day, part, logged_at) "
            "VALUES (?, ?, ?)",
            (d, part.lower(), _now_utc()),
        )


def unlog_body_part(part: str, day: Optional[str] = None) -> None:
    """Un-mark a body part for the given day (revert an accidental click)."""
    d = day or _today()
    with connect() as c:
        c.execute("DELETE FROM body_part_log WHERE day = ? AND part = ?",
                  (d, part.lower()))


def body_parts_between(start_day: str, end_day: str) -> set[str]:
    """All distinct body parts logged in the inclusive [start_day, end_day]
    range. Used by the coach to compute weekly coverage."""
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT part FROM body_part_log "
            "WHERE day >= ? AND day <= ?",
            (start_day, end_day),
        ).fetchall()
    return {r["part"] for r in rows}
