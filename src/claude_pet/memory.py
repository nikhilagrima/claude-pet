"""SQLite-backed project memory for the Claude desktop pet.

Tracks which project directories you've worked in from Claude Code, how long,
what tools you used, and lets you jot free-form notes. Meant for personal
recall — "what was I doing in that repo three weeks ago?" — and for feeding
back context to Claude via `claude-pet context`.

The database lives at ~/.claude/claude-pet/memory.sqlite (per-user, never
committed to the pet's repo). A fresh install starts with an empty DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def db_path() -> Path:
    """User data directory. Created on demand, never bundled."""
    root = Path.home() / ".claude" / "claude-pet"
    root.mkdir(parents=True, exist_ok=True)
    return root / "memory.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  path TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen  TEXT NOT NULL,
  session_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at   TEXT,
  tool_calls INTEGER NOT NULL DEFAULT 0,
  successes  INTEGER NOT NULL DEFAULT 0,
  errors     INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (project_path) REFERENCES projects(path)
);

CREATE TABLE IF NOT EXISTS tool_usage (
  project_path TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  last_used TEXT NOT NULL,
  PRIMARY KEY (project_path, tool_name),
  FOREIGN KEY (project_path) REFERENCES projects(path)
);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL,
  FOREIGN KEY (project_path) REFERENCES projects(path)
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project_path);
"""


@contextmanager
def connect():
    """Yield a connection with schema applied. Safe to call from multiple
    processes (SQLite handles the file lock; writes here are tiny)."""
    conn = sqlite3.connect(str(db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_project() -> str:
    """Best guess at 'the project the user is working in right now.'

    Prefers CLAUDE_PROJECT_DIR (Claude Code sets this in hooks), falls back
    to the current working directory.
    """
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _upsert_project(conn: sqlite3.Connection, path: str) -> None:
    now = _now()
    name = os.path.basename(path.rstrip(os.sep)) or path
    conn.execute(
        """
        INSERT INTO projects (path, name, first_seen, last_seen, session_count)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(path) DO UPDATE SET last_seen = excluded.last_seen
        """,
        (path, name, now, now),
    )


def record_session_start(project_path: str | None = None) -> int:
    """Called on Claude Code SessionStart. Returns the session id."""
    project_path = project_path or current_project()
    now = _now()
    with connect() as conn:
        _upsert_project(conn, project_path)
        conn.execute(
            "UPDATE projects SET session_count = session_count + 1, last_seen = ? WHERE path = ?",
            (now, project_path),
        )
        cur = conn.execute(
            "INSERT INTO sessions (project_path, started_at) VALUES (?, ?)",
            (project_path, now),
        )
        return cur.lastrowid


def record_tool_use(tool_name: str, project_path: str | None = None) -> None:
    project_path = project_path or current_project()
    if not tool_name:
        return
    now = _now()
    with connect() as conn:
        _upsert_project(conn, project_path)
        conn.execute(
            """
            INSERT INTO tool_usage (project_path, tool_name, count, last_used)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(project_path, tool_name) DO UPDATE SET
              count = count + 1,
              last_used = excluded.last_used
            """,
            (project_path, tool_name, now),
        )
        conn.execute(
            """
            UPDATE sessions SET tool_calls = tool_calls + 1
            WHERE id = (SELECT MAX(id) FROM sessions WHERE project_path = ?)
            """,
            (project_path,),
        )


def record_success(project_path: str | None = None) -> None:
    project_path = project_path or current_project()
    with connect() as conn:
        conn.execute(
            """
            UPDATE sessions SET successes = successes + 1, ended_at = ?
            WHERE id = (SELECT MAX(id) FROM sessions WHERE project_path = ?)
            """,
            (_now(), project_path),
        )


def record_error(project_path: str | None = None) -> None:
    project_path = project_path or current_project()
    with connect() as conn:
        conn.execute(
            """
            UPDATE sessions SET errors = errors + 1
            WHERE id = (SELECT MAX(id) FROM sessions WHERE project_path = ?)
            """,
            (project_path,),
        )


def add_note(text: str, project_path: str | None = None) -> None:
    project_path = project_path or current_project()
    with connect() as conn:
        _upsert_project(conn, project_path)
        conn.execute(
            "INSERT INTO notes (project_path, created_at, note) VALUES (?, ?, ?)",
            (project_path, _now(), text),
        )


def project_summary(project_path: str | None = None) -> dict:
    """Everything we know about one project — for CLI display or feeding to Claude."""
    project_path = project_path or current_project()
    with connect() as conn:
        proj = conn.execute(
            "SELECT * FROM projects WHERE path = ?", (project_path,)
        ).fetchone()
        if not proj:
            return {"path": project_path, "known": False}

        tools = [
            dict(r) for r in conn.execute(
                """SELECT tool_name, count, last_used
                   FROM tool_usage WHERE project_path = ?
                   ORDER BY count DESC LIMIT 10""",
                (project_path,),
            ).fetchall()
        ]
        recent = [
            dict(r) for r in conn.execute(
                """SELECT id, started_at, ended_at, tool_calls, successes, errors
                   FROM sessions WHERE project_path = ?
                   ORDER BY started_at DESC LIMIT 5""",
                (project_path,),
            ).fetchall()
        ]
        notes = [
            dict(r) for r in conn.execute(
                """SELECT id, created_at, note FROM notes
                   WHERE project_path = ? ORDER BY created_at DESC LIMIT 20""",
                (project_path,),
            ).fetchall()
        ]
        totals = conn.execute(
            """SELECT
                 COUNT(*)                      AS sessions,
                 COALESCE(SUM(tool_calls), 0)  AS tool_calls,
                 COALESCE(SUM(successes), 0)   AS successes,
                 COALESCE(SUM(errors), 0)      AS errors
               FROM sessions WHERE project_path = ?""",
            (project_path,),
        ).fetchone()
        return {
            "known": True,
            "path": proj["path"],
            "name": proj["name"],
            "first_seen": proj["first_seen"],
            "last_seen": proj["last_seen"],
            "totals": dict(totals),
            "top_tools": tools,
            "recent_sessions": recent,
            "notes": notes,
        }


def list_projects(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return [
            dict(r) for r in conn.execute(
                """SELECT p.path, p.name, p.first_seen, p.last_seen,
                          p.session_count,
                          COALESCE(SUM(s.tool_calls), 0) AS tool_calls
                   FROM projects p
                   LEFT JOIN sessions s ON s.project_path = p.path
                   GROUP BY p.path
                   ORDER BY p.last_seen DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        ]


def format_context(project_path: str | None = None) -> str:
    """Human-readable context block to paste into a new Claude session
    (or that Claude's SessionStart hook can print into the model's view)."""
    s = project_summary(project_path)
    if not s.get("known"):
        return f"No history yet for {s['path']}. Claude Pet will start remembering from now on."
    lines = []
    lines.append(f"# Project memory — {s['name']}")
    lines.append(f"Path: {s['path']}")
    lines.append(f"First seen: {s['first_seen']}   Last seen: {s['last_seen']}")
    t = s["totals"]
    lines.append(
        f"Sessions: {t['sessions']}  |  tool calls: {t['tool_calls']}  |  "
        f"successes: {t['successes']}  |  errors: {t['errors']}"
    )
    if s["top_tools"]:
        lines.append("")
        lines.append("## Most-used tools here")
        for t in s["top_tools"]:
            lines.append(f"- {t['tool_name']}: {t['count']}× (last {t['last_used']})")
    if s["recent_sessions"]:
        lines.append("")
        lines.append("## Recent sessions")
        for sess in s["recent_sessions"]:
            end = sess["ended_at"] or "—"
            lines.append(
                f"- {sess['started_at']} → {end}  "
                f"({sess['tool_calls']} calls, {sess['successes']}✓, {sess['errors']}✗)"
            )
    if s["notes"]:
        lines.append("")
        lines.append("## Your notes")
        for n in s["notes"]:
            lines.append(f"- [{n['created_at']}] {n['note']}")
    return "\n".join(lines)


def as_json(project_path: str | None = None) -> str:
    return json.dumps(project_summary(project_path), indent=2)


if __name__ == "__main__":
    # Quick self-check: dump the current project.
    print(format_context())
