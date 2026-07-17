"""SQLite access for gh_watches and gh_events. Uses the same DB as memory.py."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_watch(owner: str, repo: str) -> dict:
    """Idempotent add. Returns the row (existing or new)."""
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        raise ValueError("owner and repo must be non-empty")
    with memory.connect() as conn:
        conn.execute(
            """
            INSERT INTO gh_watches (owner, repo, added_at, enabled)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(owner, repo) DO UPDATE SET enabled = 1
            """,
            (owner, repo, _now()),
        )
        row = conn.execute(
            "SELECT * FROM gh_watches WHERE owner = ? AND repo = ?", (owner, repo)
        ).fetchone()
        return dict(row)


def remove_watch(owner: str, repo: str) -> bool:
    """True if a row was actually deleted."""
    with memory.connect() as conn:
        cur = conn.execute(
            "DELETE FROM gh_watches WHERE owner = ? AND repo = ?", (owner, repo)
        )
        return cur.rowcount > 0


def set_enabled(owner: str, repo: str, enabled: bool) -> bool:
    with memory.connect() as conn:
        cur = conn.execute(
            "UPDATE gh_watches SET enabled = ? WHERE owner = ? AND repo = ?",
            (1 if enabled else 0, owner, repo),
        )
        return cur.rowcount > 0


def list_watches(enabled_only: bool = False) -> list[dict]:
    q = "SELECT * FROM gh_watches"
    if enabled_only:
        q += " WHERE enabled = 1"
    q += " ORDER BY owner, repo"
    with memory.connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def update_cursor(
    watch_id: int,
    *,
    last_event_id: str | None = None,
    etag: str | None = None,
    last_error: str | None = None,
) -> None:
    """Update poll bookkeeping. Pass explicit None to clear, or omit to leave alone.

    `last_error=""` clears the error; a non-empty string sets it.
    """
    sets = ["last_checked = ?"]
    params: list = [_now()]
    if last_event_id is not None:
        sets.append("last_event_id = ?")
        params.append(last_event_id)
    if etag is not None:
        sets.append("etag = ?")
        params.append(etag)
    if last_error is not None:
        sets.append("last_error = ?")
        params.append(last_error or None)
    params.append(watch_id)
    with memory.connect() as conn:
        conn.execute(
            f"UPDATE gh_watches SET {', '.join(sets)} WHERE id = ?", params
        )


def record_event(
    watch_id: int,
    event_id: str,
    event_type: str,
    actor: str | None,
    title: str,
    url: str | None,
    reaction: str,
    created_at: str,
) -> bool:
    """Insert one event. Returns True if it was NEW (i.e. we should alert).

    Idempotent by (watch_id, event_id): a re-insert returns False.
    """
    with memory.connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO gh_events
              (watch_id, event_id, event_type, actor, title, url, reaction,
               created_at, seen_at, alerted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (watch_id, event_id, event_type, actor, title, url, reaction,
             created_at, _now()),
        )
        return cur.rowcount > 0


def mark_alerted(event_row_id: int) -> None:
    with memory.connect() as conn:
        conn.execute("UPDATE gh_events SET alerted = 1 WHERE id = ?", (event_row_id,))


def recent_events(limit: int = 20, watch_id: int | None = None) -> list[dict]:
    q = """
    SELECT e.*, w.owner, w.repo FROM gh_events e
    JOIN gh_watches w ON w.id = e.watch_id
    """
    params: list = []
    if watch_id is not None:
        q += " WHERE e.watch_id = ?"
        params.append(watch_id)
    q += " ORDER BY e.seen_at DESC LIMIT ?"
    params.append(limit)
    with memory.connect() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def pending_alerts() -> list[dict]:
    """Events that arrived but haven't fired a pet reaction yet."""
    q = """
    SELECT e.*, w.owner, w.repo FROM gh_events e
    JOIN gh_watches w ON w.id = e.watch_id
    WHERE e.alerted = 0 AND w.enabled = 1 AND e.reaction != 'none'
    ORDER BY e.seen_at ASC, e.id ASC
    """
    with memory.connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]
