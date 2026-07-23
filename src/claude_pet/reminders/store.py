"""SQLite store for personal reminders.

Its own file (~/.claude/claude-pet/reminders.db) so a memory-brain wipe or
project cascade never touches reminders. Single table; the fired-stages
list is stored as a JSON string for simplicity (only up to 3 stages ever).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


def db_path() -> Path:
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "reminders.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  note         TEXT NOT NULL DEFAULT '',
  due_at       TEXT NOT NULL,              -- ISO8601 (assume local; naive OK)
  created_at   TEXT NOT NULL,              -- ISO8601 UTC
  fired_stages TEXT NOT NULL DEFAULT '[]', -- JSON list of stage names
  completed_at TEXT                        -- NULL until user marks done or all fired
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);
CREATE INDEX IF NOT EXISTS idx_reminders_completed ON reminders(completed_at);
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


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- writes -----------------------------------------------------------------

def add(title: str, due_at: datetime, note: str = "") -> int:
    """Insert a reminder. Returns its id."""
    if not title.strip():
        raise ValueError("title cannot be empty")
    due_iso = due_at.isoformat(timespec="seconds")
    with connect() as c:
        cur = c.execute(
            "INSERT INTO reminders (title, note, due_at, created_at) "
            "VALUES (?, ?, ?, ?)",
            (title.strip(), note or "", due_iso, _now_utc()),
        )
        return cur.lastrowid


def mark_stage_fired(reminder_id: int, stage: str) -> None:
    """Append `stage` to the fired_stages JSON list. Idempotent — a stage
    already present is not re-added. If all 3 stages have now fired, the
    reminder is auto-marked completed."""
    with connect() as c:
        row = c.execute(
            "SELECT fired_stages FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        if not row:
            return
        fired = json.loads(row["fired_stages"] or "[]")
        if stage in fired:
            return
        fired.append(stage)
        params = {"fs": json.dumps(fired), "id": reminder_id}
        if set(fired) >= {"day_before", "five_min", "on_time"}:
            c.execute(
                "UPDATE reminders SET fired_stages = :fs, completed_at = :ca "
                "WHERE id = :id",
                {**params, "ca": _now_utc()},
            )
        else:
            c.execute(
                "UPDATE reminders SET fired_stages = :fs WHERE id = :id",
                params,
            )


def mark_completed(reminder_id: int) -> bool:
    """User pressed Done. True if a row changed."""
    with connect() as c:
        cur = c.execute(
            "UPDATE reminders SET completed_at = ? "
            "WHERE id = ? AND completed_at IS NULL",
            (_now_utc(), reminder_id),
        )
        return cur.rowcount > 0


def snooze(reminder_id: int, minutes: int) -> bool:
    """Push the due_at forward by N minutes and reset fired stages so
    the reminder can fire again from scratch."""
    if minutes <= 0:
        return False
    with connect() as c:
        row = c.execute(
            "SELECT due_at FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        if not row:
            return False
        try:
            due = datetime.fromisoformat(row["due_at"])
        except Exception:
            due = datetime.now()
        from datetime import timedelta
        new_due = max(datetime.now(), due) + timedelta(minutes=minutes)
        cur = c.execute(
            "UPDATE reminders SET due_at = ?, fired_stages = '[]', "
            "completed_at = NULL WHERE id = ?",
            (new_due.isoformat(timespec="seconds"), reminder_id),
        )
        return cur.rowcount > 0


def delete(reminder_id: int) -> bool:
    with connect() as c:
        cur = c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return cur.rowcount > 0


# --- reads ------------------------------------------------------------------

def _row_to_dict(r: sqlite3.Row) -> dict:
    return {
        "id":           r["id"],
        "title":        r["title"],
        "note":         r["note"] or "",
        "due_at":       r["due_at"],
        "created_at":   r["created_at"],
        "fired_stages": json.loads(r["fired_stages"] or "[]"),
        "completed_at": r["completed_at"],
    }


def list_active() -> list[dict]:
    """Not-yet-completed reminders, oldest-due-first."""
    with connect() as c:
        return [_row_to_dict(r) for r in c.execute(
            "SELECT * FROM reminders WHERE completed_at IS NULL "
            "ORDER BY due_at ASC"
        ).fetchall()]


def list_all(limit: int = 100) -> list[dict]:
    with connect() as c:
        return [_row_to_dict(r) for r in c.execute(
            "SELECT * FROM reminders ORDER BY due_at DESC LIMIT ?", (limit,)
        ).fetchall()]


def get(reminder_id: int) -> Optional[dict]:
    with connect() as c:
        r = c.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
    return _row_to_dict(r) if r else None
