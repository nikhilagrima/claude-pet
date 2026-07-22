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


SCHEMA_V1 = """
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

# v2 additions — graph of decisions/conventions/fixes/gotchas + skills
SCHEMA_V2_ADDITIONS = """
CREATE TABLE IF NOT EXISTS nodes (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path   TEXT NOT NULL,
  kind           TEXT NOT NULL,
  key            TEXT NOT NULL,
  value          TEXT NOT NULL,
  weight         REAL NOT NULL DEFAULT 1.0,
  reinforcements INTEGER NOT NULL DEFAULT 1,
  file_path      TEXT,
  created_at     TEXT NOT NULL,
  last_seen      TEXT NOT NULL,
  UNIQUE(project_path, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_path);
CREATE INDEX IF NOT EXISTS idx_nodes_weight  ON nodes(project_path, weight DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_last    ON nodes(project_path, last_seen DESC);

CREATE TABLE IF NOT EXISTS edges (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path  TEXT NOT NULL,
  src_id        INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  dst_id        INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,
  weight        REAL NOT NULL DEFAULT 1.0,
  UNIQUE(project_path, src_id, dst_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);

CREATE TABLE IF NOT EXISTS skills (
  slug            TEXT PRIMARY KEY,
  title           TEXT NOT NULL,
  description     TEXT NOT NULL,
  level           INTEGER NOT NULL DEFAULT 1,
  tier            TEXT NOT NULL,
  reinforcements  INTEGER NOT NULL DEFAULT 1,
  project_paths   TEXT NOT NULL,
  source_node_ids TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  last_used       TEXT NOT NULL,
  disk_path       TEXT
);
"""

# FTS5 is applied opportunistically — some SQLite builds lack it.
SCHEMA_V2_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
  value, content='nodes', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, value) VALUES (new.id, new.value);
END;
CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, value) VALUES('delete', old.id, old.value);
END;
CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, value) VALUES('delete', old.id, old.value);
  INSERT INTO nodes_fts(rowid, value) VALUES (new.id, new.value);
END;
"""

# v3 additions — GitHub repo watcher (commits/PRs/reviews/deploys)
SCHEMA_V3_ADDITIONS = """
CREATE TABLE IF NOT EXISTS gh_watches (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  owner         TEXT NOT NULL,
  repo          TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1,
  last_event_id TEXT,
  etag          TEXT,
  last_checked  TEXT,
  last_error    TEXT,
  added_at      TEXT NOT NULL,
  UNIQUE(owner, repo)
);

CREATE TABLE IF NOT EXISTS gh_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  watch_id     INTEGER NOT NULL REFERENCES gh_watches(id) ON DELETE CASCADE,
  event_id     TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  actor        TEXT,
  title        TEXT NOT NULL,
  url          TEXT,
  reaction     TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  seen_at      TEXT NOT NULL,
  alerted      INTEGER NOT NULL DEFAULT 0,
  UNIQUE(watch_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_gh_events_watch ON gh_events(watch_id, seen_at DESC);
"""

SCHEMA_VERSION = 3


def _fts5_available(conn: sqlite3.Connection) -> bool:
    """True if this SQLite build has FTS5. Some minimal builds don't."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.Error:
        return False


def _current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive migration. Never drops or renames v0.2.0 tables.

    All schema scripts use `CREATE ... IF NOT EXISTS`, so we run every block
    on every connect. This is cheap (SQLite short-circuits when the object
    exists) and self-heals if a prior code version bumped `user_version` past
    a step without creating the corresponding tables.
    """
    conn.executescript(SCHEMA_V1)
    conn.executescript(SCHEMA_V2_ADDITIONS)
    if _fts5_available(conn):
        conn.executescript(SCHEMA_V2_FTS)
    conn.executescript(SCHEMA_V3_ADDITIONS)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def connect():
    """Yield a connection with the current schema applied. Safe across
    processes (SQLite handles the file lock; writes here are tiny).

    Migration runs on every connect but is idempotent — cheap enough that
    we don't need a separate init step."""
    conn = sqlite3.connect(str(db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_project_path(path: str) -> str:
    """Canonical identity for a project directory.

    realpath() resolves symlinks (macOS /tmp → /private/tmp aliasing was
    observed splitting one project into two identities in real use) and
    trailing-slash / relative-path variations. Every read AND write path
    must funnel through this so context can never attach to the wrong
    project identity."""
    if not path:
        return path
    return os.path.realpath(os.path.expanduser(path))


def current_project() -> str:
    """Best guess at 'the project the user is working in right now.'

    Prefers CLAUDE_PROJECT_DIR (Claude Code sets this in hooks), falls back
    to the current working directory. Always normalized."""
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return normalize_project_path(raw)


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
    project_path = normalize_project_path(project_path) if project_path else current_project()
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
    project_path = normalize_project_path(project_path) if project_path else current_project()
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
    project_path = normalize_project_path(project_path) if project_path else current_project()
    with connect() as conn:
        conn.execute(
            """
            UPDATE sessions SET successes = successes + 1, ended_at = ?
            WHERE id = (SELECT MAX(id) FROM sessions WHERE project_path = ?)
            """,
            (_now(), project_path),
        )


def record_error(project_path: str | None = None) -> None:
    project_path = normalize_project_path(project_path) if project_path else current_project()
    with connect() as conn:
        conn.execute(
            """
            UPDATE sessions SET errors = errors + 1
            WHERE id = (SELECT MAX(id) FROM sessions WHERE project_path = ?)
            """,
            (project_path,),
        )


def add_note(text: str, project_path: str | None = None) -> None:
    project_path = normalize_project_path(project_path) if project_path else current_project()
    with connect() as conn:
        _upsert_project(conn, project_path)
        conn.execute(
            "INSERT INTO notes (project_path, created_at, note) VALUES (?, ?, ?)",
            (project_path, _now(), text),
        )


def project_summary(project_path: str | None = None) -> dict:
    """Everything we know about one project — for CLI display or feeding to Claude."""
    project_path = normalize_project_path(project_path) if project_path else current_project()
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


def delete_project(project_path: str) -> dict:
    """Remove every trace of a project from memory. Returns row counts deleted.

    Cascades across every table that references project_path. Idempotent — a
    second call on the same path returns zeros.

    Path-normalization robustness: the projects table historically accepted
    both raw ("/tmp/foo") and realpath'd ("/private/tmp/foo") variants
    depending on when the row was written, which meant a naive
    WHERE path=<normalized> could miss the row that's clearly visible in
    list_projects(). We now DELETE on the union of {raw, normalized, given}
    so a UI or CLI click always cleans everything named by that path.
    """
    normalized = normalize_project_path(project_path)
    # Build the set of path variants we'll try — original input, normalized,
    # and any legacy /tmp ↔ /private/tmp aliasing pair.
    candidates: set[str] = {project_path, normalized}
    if normalized.startswith("/private/tmp/"):
        candidates.add(normalized.replace("/private/tmp/", "/tmp/", 1))
    if project_path.startswith("/tmp/"):
        candidates.add("/private" + project_path)
    candidates.discard("")

    def _in_clause(n: int) -> str:
        return "(" + ", ".join("?" * n) + ")"

    counts = {}
    with connect() as conn:
        params = tuple(candidates)
        placeholders = _in_clause(len(params))
        # Order matters: delete edges/nodes first (nodes has ON DELETE CASCADE
        # for edges, but explicit is safer). Then dependents, then `projects`
        # which uses `path` as its PK column instead of `project_path`.
        for table in ("edges", "nodes", "tool_usage", "notes", "sessions"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE project_path IN {placeholders}",
                params,
            )
            counts[table] = cur.rowcount
        cur = conn.execute(
            f"DELETE FROM projects WHERE path IN {placeholders}", params,
        )
        counts["projects"] = cur.rowcount
        # Also drop any skills whose ONLY source project was one of these.
        skills = conn.execute(
            "SELECT slug, project_paths FROM skills"
        ).fetchall()
        killed_skills = 0
        for s in skills:
            paths = set(json.loads(s["project_paths"]))
            hits = paths & candidates
            if hits:
                paths -= hits
                if not paths:
                    conn.execute("DELETE FROM skills WHERE slug = ?", (s["slug"],))
                    killed_skills += 1
                else:
                    conn.execute(
                        "UPDATE skills SET project_paths = ? WHERE slug = ?",
                        (json.dumps(sorted(paths)), s["slug"]),
                    )
        counts["skills"] = killed_skills
    return counts


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


# ---------------------------------------------------------------------------
# v0.3.0 graph API — nodes, edges, skills
# ---------------------------------------------------------------------------

import math  # noqa: E402


def upsert_node(
    project_path: str,
    kind: str,
    key: str,
    value: str,
    *,
    file_path: str | None = None,
    weight_delta: float = 1.0,
) -> int:
    """Insert or reinforce a node. Repeats bump weight + reinforcements + last_seen.

    Returns the node id. Idempotent by (project_path, kind, key)."""
    project_path = normalize_project_path(project_path)
    now = _now()
    with connect() as conn:
        _upsert_project(conn, project_path)
        cur = conn.execute(
            """
            INSERT INTO nodes (project_path, kind, key, value, weight,
                               reinforcements, file_path, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(project_path, kind, key) DO UPDATE SET
              value          = excluded.value,
              weight         = weight + ?,
              reinforcements = reinforcements + 1,
              file_path      = COALESCE(excluded.file_path, file_path),
              last_seen      = excluded.last_seen
            """,
            (project_path, kind, key, value, weight_delta,
             file_path, now, now, weight_delta),
        )
        # RETURNING isn't in all Python sqlite3 shipped versions — look it up.
        row = conn.execute(
            "SELECT id FROM nodes WHERE project_path = ? AND kind = ? AND key = ?",
            (project_path, kind, key),
        ).fetchone()
        return row["id"] if row else cur.lastrowid


def add_edge(project_path: str, src_id: int, dst_id: int, kind: str,
             weight_delta: float = 1.0) -> None:
    """Idempotent by (project, src, dst, kind). Repeats bump weight."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO edges (project_path, src_id, dst_id, kind, weight)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_path, src_id, dst_id, kind) DO UPDATE SET
              weight = weight + ?
            """,
            (project_path, src_id, dst_id, kind, weight_delta, weight_delta),
        )


def top_nodes(
    project_path: str,
    limit: int = 20,
    query: str | None = None,
    kinds: tuple[str, ...] | None = None,
) -> list[dict]:
    project_path = normalize_project_path(project_path)
    """Rank nodes for injection: weight × exp(-hours_since_last / 168).

    If FTS5 is available and `query` is provided, we add BM25 boost.
    Deterministic ordering: ties broken by last_seen DESC then id DESC.
    """
    with connect() as conn:
        # Detect FTS5 by checking if the virtual table exists.
        has_fts = bool(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes_fts'"
        ).fetchone())

        # Collect FTS-matched node ids up front (if a query was supplied).
        # We can't LEFT-JOIN an FTS5 virtual table with a MATCH inside the ON
        # clause reliably — do the FTS lookup separately and use it as a
        # score bonus in-python.
        fts_hits: set[int] = set()
        if query and has_fts:
            try:
                fts_hits = {
                    r[0] for r in conn.execute(
                        "SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH ?",
                        (query,),
                    ).fetchall()
                }
            except sqlite3.OperationalError:
                # Malformed FTS query (e.g. reserved chars) — degrade to no boost.
                fts_hits = set()

        params: list = [project_path]
        kind_clause = ""
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            kind_clause = f" AND kind IN ({placeholders})"
            params.extend(kinds)

        # Over-fetch when we have a query so FTS matches can surface even
        # if they weren't in the raw top-N by weight.
        fetch = limit * 3 if fts_hits else limit
        sql = f"""
        SELECT * FROM nodes
        WHERE project_path = ?{kind_clause}
        ORDER BY weight DESC, last_seen DESC, id DESC
        LIMIT ?
        """
        params = params + [fetch]
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Score = weight * recency_decay, plus a small additive bonus for FTS hits.
    FTS_BONUS = 2.0  # ~ two extra reinforcements' worth
    now_dt = datetime.now(timezone.utc)
    for r in rows:
        try:
            last = datetime.fromisoformat(r["last_seen"])
        except Exception:
            last = now_dt
        hours = max((now_dt - last).total_seconds() / 3600.0, 0.0)
        base = r["weight"] + (FTS_BONUS if r["id"] in fts_hits else 0.0)
        r["score"] = base * math.exp(-hours / 168.0)
    rows.sort(key=lambda r: (r["score"], r["last_seen"], r["id"]), reverse=True)
    return rows[:limit]


def upsert_skill(
    slug: str, title: str, description: str,
    project_path: str, source_node_ids: list[int],
    disk_path: str | None = None,
    reinforcements: int | None = None,
) -> dict:
    """Insert or reinforce a skill. Level = floor(log2(reinforcements)) + 1.
    Tier = hatchling(1) / apprentice(2) / senior(3) / master(4+).

    If `reinforcements` is provided, it OVERRIDES the stored count (used when
    promoting a node whose own reinforcement count is authoritative). If
    omitted, we increment by 1 (the "someone used this skill" path)."""
    project_path = normalize_project_path(project_path)
    now = _now()
    with connect() as conn:
        existing = conn.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
        if existing:
            if reinforcements is None:
                new_reinforcements = existing["reinforcements"] + 1
            else:
                new_reinforcements = max(existing["reinforcements"], reinforcements)
            paths = set(json.loads(existing["project_paths"])) | {project_path}
            ids = list({*json.loads(existing["source_node_ids"]), *source_node_ids})
        else:
            new_reinforcements = reinforcements if reinforcements is not None else 1
            paths = {project_path}
            ids = source_node_ids
        reinforcements = new_reinforcements
        # Skills are only created at reinforcements>=2 (see skills.PROMOTION_THRESHOLD),
        # so log2(2)=1 becomes the base level. Cap at 1 for the rare direct-insert case.
        level = max(int(math.floor(math.log2(max(reinforcements, 2)))), 1)
        tier = {1: "hatchling", 2: "apprentice", 3: "senior"}.get(level, "master")
        conn.execute(
            """
            INSERT INTO skills (slug, title, description, level, tier,
                                reinforcements, project_paths, source_node_ids,
                                created_at, last_used, disk_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
              title          = excluded.title,
              description    = excluded.description,
              level          = excluded.level,
              tier           = excluded.tier,
              reinforcements = excluded.reinforcements,
              project_paths  = excluded.project_paths,
              source_node_ids= excluded.source_node_ids,
              last_used      = excluded.last_used,
              disk_path      = COALESCE(excluded.disk_path, disk_path)
            """,
            (slug, title, description, level, tier, reinforcements,
             json.dumps(sorted(paths)), json.dumps(sorted(ids)),
             existing["created_at"] if existing else now, now,
             disk_path or (existing["disk_path"] if existing else None)),
        )
        return {
            "slug": slug, "title": title, "level": level, "tier": tier,
            "reinforcements": reinforcements,
        }


def list_skills(project_path: str | None = None) -> list[dict]:
    """All skills, or only those earned in the given project.

    Injection MUST pass project_path — a skill like 'this codebase is
    Edit-dominant' learned in project A is wrong context in project B.
    The panel's Skills tab passes None deliberately (global overview)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM skills ORDER BY level DESC, last_used DESC LIMIT 200"
        ).fetchall()
    skills = [dict(r) for r in rows]
    if project_path is None:
        return skills
    wanted = normalize_project_path(project_path)
    out = []
    for s in skills:
        try:
            paths = {normalize_project_path(p) for p in json.loads(s["project_paths"])}
        except Exception:
            paths = set()
        if wanted in paths:
            out.append(s)
    return out


def top_tier() -> str:
    """Highest expertise tier the user has earned. For mascot evolution."""
    with connect() as conn:
        row = conn.execute("SELECT MAX(level) AS m FROM skills").fetchone()
    if not row or not row["m"]:
        return "hatchling"
    lvl = row["m"]
    return {1: "hatchling", 2: "apprentice", 3: "senior"}.get(lvl, "master")


if __name__ == "__main__":
    # Quick self-check: dump the current project.
    print(format_context())
