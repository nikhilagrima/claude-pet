"""Phase 1 tests — storage v2 schema, migration, upsert reinforcement.

Run: `.venv/bin/python -m unittest tests.test_storage_v2 -v`
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _isolated_db():
    """Redirect memory.db_path() into a throwaway tmp dir for each test."""
    tmp = tempfile.mkdtemp(prefix="claude-pet-test-")
    return Path(tmp) / "memory.sqlite"


class StorageV2Tests(unittest.TestCase):
    def setUp(self):
        self.db_file = _isolated_db()
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ fresh
    def test_fresh_install_reports_version_2(self):
        from claude_pet import memory
        with memory.connect() as conn:
            v = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, 2)

    def test_fresh_install_has_all_v2_tables(self):
        from claude_pet import memory
        with memory.connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        for t in ("projects", "sessions", "tool_usage", "notes",
                  "nodes", "edges", "skills"):
            self.assertIn(t, tables, f"missing table: {t}")

    def test_fresh_install_creates_fts_if_available(self):
        from claude_pet import memory
        with memory.connect() as conn:
            probe = sqlite3.connect(":memory:")
            fts_available = memory._fts5_available(probe)
            probe.close()
            has_fts = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes_fts'"
            ).fetchone())
        # On builds where FTS5 exists, our schema must have created it.
        # On minimal builds, we simply skip — that's expected.
        self.assertEqual(fts_available, has_fts,
                         "nodes_fts should exist iff FTS5 is available")

    # ---------------------------------------------------------- v0.2.0 upgrade
    def test_v020_rows_preserved_after_migration(self):
        """Simulate a v0.2.0 DB (no user_version, v1 tables only) and verify
        that connecting upgrades it to v2 while leaving rows intact."""
        # Build a v0.2.0-style DB by hand
        conn = sqlite3.connect(str(self.db_file))
        conn.executescript("""
            CREATE TABLE projects (
              path TEXT PRIMARY KEY, name TEXT NOT NULL,
              first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
              session_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_path TEXT NOT NULL, created_at TEXT NOT NULL,
              note TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            ("/tmp/legacy", "legacy", "2026-01-01", "2026-01-02", 3),
        )
        conn.execute(
            "INSERT INTO notes (project_path, created_at, note) VALUES (?, ?, ?)",
            ("/tmp/legacy", "2026-01-01", "existing note preserved"),
        )
        conn.commit()
        conn.close()

        # Now connect via the pet — migration should run.
        from claude_pet import memory
        with memory.connect() as c2:
            v = c2.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(v, 2)
            row = c2.execute(
                "SELECT * FROM projects WHERE path = ?", ("/tmp/legacy",)
            ).fetchone()
            self.assertIsNotNone(row, "v0.2.0 project row was lost")
            self.assertEqual(row["session_count"], 3)
            note = c2.execute(
                "SELECT note FROM notes WHERE project_path = ?", ("/tmp/legacy",)
            ).fetchone()
            self.assertEqual(note["note"], "existing note preserved")

    # ---------------------------------------------------------------- upsert
    def test_upsert_node_dedups_and_reinforces(self):
        from claude_pet import memory
        id1 = memory.upsert_node("/p", "convention", "use bun not npm",
                                 "Always use bun in this project")
        id2 = memory.upsert_node("/p", "convention", "use bun not npm",
                                 "Always use bun in this project")
        self.assertEqual(id1, id2, "same (project, kind, key) should collapse to one row")
        with memory.connect() as conn:
            row = conn.execute(
                "SELECT weight, reinforcements FROM nodes WHERE id = ?", (id1,)
            ).fetchone()
        self.assertEqual(row["reinforcements"], 2)
        self.assertAlmostEqual(row["weight"], 2.0, places=5)

    def test_top_nodes_ranks_by_weight(self):
        from claude_pet import memory
        memory.upsert_node("/p", "fix", "a", "low weight")
        for _ in range(4):
            memory.upsert_node("/p", "fix", "b", "high weight")
        ranked = memory.top_nodes("/p", limit=10)
        keys = [r["key"] for r in ranked]
        self.assertEqual(keys[0], "b", "highest weight must come first")
        self.assertGreater(ranked[0]["weight"], ranked[1]["weight"])

    # ---------------------------------------------------------------- skills
    def test_upsert_skill_levels_via_log2(self):
        """Level = floor(log2(reinforcements)), clamped to min 1.
        Skills only exist at reinforcements≥2 in normal use."""
        from claude_pet import memory
        # 2× → level 1 (hatchling)
        s = memory.upsert_skill("test-skill", "Test", "desc", "/p", [1], reinforcements=2)
        self.assertEqual(s["reinforcements"], 2)
        self.assertEqual(s["level"], 1)
        self.assertEqual(s["tier"], "hatchling")
        # 4× → level 2 (apprentice)
        s = memory.upsert_skill("test-skill", "Test", "desc", "/p", [1], reinforcements=4)
        self.assertEqual(s["level"], 2)
        self.assertEqual(s["tier"], "apprentice")
        # 8× → level 3 (senior)
        s = memory.upsert_skill("test-skill", "Test", "desc", "/p", [1], reinforcements=8)
        self.assertEqual(s["level"], 3)
        self.assertEqual(s["tier"], "senior")
        # 16× → level 4 (ponytail)
        s = memory.upsert_skill("test-skill", "Test", "desc", "/p", [1], reinforcements=16)
        self.assertEqual(s["level"], 4)
        self.assertEqual(s["tier"], "ponytail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
