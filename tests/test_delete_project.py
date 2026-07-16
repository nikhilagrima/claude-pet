"""Test the delete_project cascade and the `claude-pet forget` CLI."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _capture(fn):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        try:
            fn()
        except SystemExit:
            pass
    finally:
        sys.stdout = old
    return buf.getvalue()


class DeleteProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-forget-"))
        self.db = self.tmp / "memory.sqlite"
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir(parents=True)
        for target, val in [
            ("claude_pet.memory.db_path", lambda: self.db),
            ("claude_pet.skills._skills_dir", lambda: self.skills_dir),
        ]:
            p = mock.patch(target, side_effect=val)
            p.start()
            self.addCleanup(p.stop)

    def _seed(self, project):
        from claude_pet import memory
        memory.record_session_start(project)
        memory.record_tool_use("Bash", project)
        memory.record_tool_use("Read", project)
        memory.add_note("seed note", project)
        memory.upsert_node(project, "convention", "k1", "one")
        memory.upsert_node(project, "convention", "k2", "two")
        memory.upsert_node(project, "convention", "k1", "one")  # reinforce
        memory.upsert_skill(
            "test-skill", "T", "desc", project,
            source_node_ids=[1], reinforcements=2,
        )

    def test_delete_removes_everything_for_target_project(self):
        from claude_pet import memory
        self._seed("/proj/A")
        self._seed("/proj/B")  # different project — must survive
        counts = memory.delete_project("/proj/A")
        self.assertGreater(counts["projects"], 0)
        self.assertGreater(counts["notes"], 0)
        self.assertGreater(counts["nodes"], 0)
        self.assertGreater(counts["sessions"], 0)
        self.assertGreater(counts["tool_usage"], 0)
        # /proj/A gone.
        with memory.connect() as conn:
            # `projects` uses `path` as its PK — every other table uses `project_path`.
            for table, col in [
                ("projects", "path"), ("sessions", "project_path"),
                ("tool_usage", "project_path"), ("notes", "project_path"),
                ("nodes", "project_path"),
            ]:
                n = conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {col}='/proj/A'").fetchone()["c"]
                self.assertEqual(n, 0, f"{table} still has /proj/A rows")
        # /proj/B intact.
        with memory.connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM notes WHERE project_path='/proj/B'").fetchone()["c"]
            self.assertGreater(n, 0)

    def test_delete_drops_skills_unique_to_project(self):
        from claude_pet import memory
        self._seed("/only-project")
        # test-skill was created only for /only-project — after delete it should die.
        counts = memory.delete_project("/only-project")
        self.assertEqual(counts["skills"], 1)
        with memory.connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM skills WHERE slug='test-skill'").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_delete_keeps_skills_used_in_other_projects(self):
        from claude_pet import memory
        self._seed("/proj/A")
        # Reinforce the same skill from a second project — now shared.
        memory.upsert_skill(
            "test-skill", "T", "desc", "/proj/B",
            source_node_ids=[1], reinforcements=3,
        )
        memory.delete_project("/proj/A")
        with memory.connect() as conn:
            row = conn.execute("SELECT project_paths FROM skills WHERE slug='test-skill'").fetchone()
        self.assertIsNotNone(row, "shared skill should survive")
        paths = json.loads(row["project_paths"])
        self.assertNotIn("/proj/A", paths)
        self.assertIn("/proj/B", paths)

    def test_forget_cli_is_idempotent(self):
        from claude_pet import cli, memory
        self._seed("/gone")
        # first call: removes stuff
        argv = ["claude-pet", "forget", "--path", "/gone"]
        with mock.patch.object(sys, "argv", argv):
            out1 = _capture(cli.main)
        self.assertIn("forgot", out1)
        # second call: nothing left to remove, still exits cleanly
        with mock.patch.object(sys, "argv", argv):
            out2 = _capture(cli.main)
        self.assertIn("no memory found", out2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
