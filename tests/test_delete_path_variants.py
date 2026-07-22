"""Regression: delete_project must remove rows regardless of which path
variant they were stored under (/tmp vs /private/tmp aliasing on macOS).

The bug: pet's earlier code sometimes stored raw "/tmp/x" paths, later
code normalized every write to "/private/tmp/x". `delete_project` then
looked up ONLY the normalized form → legacy rows became undeletable
from both CLI and UI, presenting to the user as 'no memory found' or
'nothing to delete' despite the row being clearly visible in
list_projects().
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DeleteHandlesRawTmpPath(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.mkdtemp(prefix="claude-pet-del-")) / "memory.sqlite"
        p = mock.patch("claude_pet.memory.db_path", return_value=self.db)
        p.start(); self.addCleanup(p.stop)

    def _insert_raw(self, path: str):
        """Insert a project row using the RAW (un-normalized) path — same
        as the pet's pre-normalization code would have done."""
        from claude_pet import memory
        with memory.connect() as c:
            c.execute(
                "INSERT INTO projects (path, name, first_seen, last_seen, session_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (path, path.rsplit("/", 1)[-1], "2026-01-01", "2026-01-01", 1),
            )
            c.execute(
                "INSERT INTO sessions (project_path, started_at) VALUES (?, ?)",
                (path, "2026-01-01"),
            )
            c.execute(
                "INSERT INTO nodes (project_path, kind, key, value, weight, "
                "reinforcements, created_at, last_seen) "
                "VALUES (?, 'convention', 'k1', 'v1', 1.0, 1, ?, ?)",
                (path, "2026-01-01", "2026-01-01"),
            )

    def test_delete_removes_row_stored_with_raw_tmp_prefix(self):
        """A row stored as '/tmp/foo' must be deletable even when the
        caller passes '/tmp/foo' (which normalizes to '/private/tmp/foo')."""
        from claude_pet import memory
        self._insert_raw("/tmp/legacy-audit")
        # Sanity: row is visible in list_projects
        projs = {p["path"] for p in memory.list_projects()}
        self.assertIn("/tmp/legacy-audit", projs)
        # Delete via the exact string the user would type / the UI sees
        counts = memory.delete_project("/tmp/legacy-audit")
        # Assert something actually got deleted (regression: was returning all-zeros)
        self.assertGreater(sum(counts.values()), 0,
                           f"delete returned all zeros: {counts}")
        # Verify the row is gone
        projs_after = {p["path"] for p in memory.list_projects()}
        self.assertNotIn("/tmp/legacy-audit", projs_after)

    def test_delete_removes_row_stored_with_private_tmp_prefix(self):
        """The other direction: row stored as '/private/tmp/foo' must be
        deletable when caller passes either form."""
        from claude_pet import memory
        self._insert_raw("/private/tmp/new-audit")
        counts = memory.delete_project("/tmp/new-audit")   # user types short form
        self.assertGreater(sum(counts.values()), 0)
        projs_after = {p["path"] for p in memory.list_projects()}
        self.assertNotIn("/private/tmp/new-audit", projs_after)

    def test_delete_still_idempotent(self):
        """Second call on same path returns zeros (no crash)."""
        from claude_pet import memory
        self._insert_raw("/tmp/idem-check")
        first = memory.delete_project("/tmp/idem-check")
        second = memory.delete_project("/tmp/idem-check")
        self.assertGreater(sum(first.values()), 0)
        self.assertEqual(sum(second.values()), 0)


if __name__ == "__main__":
    unittest.main()
