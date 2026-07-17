"""Cross-project context isolation — the injection for project A must NEVER
contain project B's notes, nodes, or skills, and path aliases (symlinks,
trailing slashes) must resolve to one project identity."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ContextIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-iso-"))
        self.db = self.tmp / "memory.sqlite"
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seed_two_projects(self):
        from claude_pet import memory
        a, b = str(self.tmp / "proj-alpha"), str(self.tmp / "proj-beta")
        os.makedirs(a, exist_ok=True)
        os.makedirs(b, exist_ok=True)
        memory.record_session_start(a)
        memory.add_note("ALPHA-SECRET-NOTE use postgres migrations here", a)
        memory.upsert_node(a, "convention", "alpha-conv", "ALPHA-CONVENTION prefer tabs")
        memory.upsert_skill("alpha-skill", "Alpha Skill",
                            "ALPHA-SKILL-DESC editing heavy", a, [1], reinforcements=2)
        memory.record_session_start(b)
        memory.add_note("BETA-SECRET-NOTE use mongo replica sets", b)
        memory.upsert_node(b, "convention", "beta-conv", "BETA-CONVENTION prefer spaces")
        memory.upsert_skill("beta-skill", "Beta Skill",
                            "BETA-SKILL-DESC research heavy", b, [2], reinforcements=2)
        return a, b

    def test_project_a_injection_contains_no_project_b_content(self):
        from claude_pet import context as ctx
        a, b = self._seed_two_projects()
        block_a = ctx.build_context(a)
        block_b = ctx.build_context(b)

        # A sees its own content…
        self.assertIn("ALPHA-SECRET-NOTE", block_a)
        self.assertIn("ALPHA-CONVENTION", block_a)
        self.assertIn("Alpha Skill", block_a)
        # …and none of B's.
        self.assertNotIn("BETA-SECRET-NOTE", block_a)
        self.assertNotIn("BETA-CONVENTION", block_a)
        self.assertNotIn("Beta Skill", block_a)

        # Symmetric for B.
        self.assertIn("BETA-SECRET-NOTE", block_b)
        self.assertNotIn("ALPHA-SECRET-NOTE", block_b)
        self.assertNotIn("Alpha Skill", block_b)

    def test_skills_injection_scoped_but_panel_view_global(self):
        from claude_pet import memory
        a, b = self._seed_two_projects()
        scoped = memory.list_skills(a)
        self.assertEqual([s["slug"] for s in scoped], ["alpha-skill"])
        global_view = memory.list_skills()  # panel overview — intentionally global
        self.assertEqual({s["slug"] for s in global_view}, {"alpha-skill", "beta-skill"})

    def test_symlink_alias_resolves_to_one_project(self):
        """macOS /tmp → /private/tmp style aliasing must not split a project."""
        from claude_pet import memory
        real = self.tmp / "real-project"
        real.mkdir()
        link = self.tmp / "link-to-project"
        os.symlink(real, link)

        memory.add_note("note written via REAL path", str(real))
        memory.add_note("note written via SYMLINK path", str(link))

        rows = memory.list_projects()
        matching = [r for r in rows if "real-project" in r["path"]]
        self.assertEqual(len(matching), 1,
                         f"symlink alias split the project: {[r['path'] for r in rows]}")
        summary = memory.project_summary(str(link))
        notes = {n["note"] for n in summary["notes"]}
        self.assertIn("note written via REAL path", notes)
        self.assertIn("note written via SYMLINK path", notes)

    def test_trailing_slash_and_relative_variants_unify(self):
        from claude_pet import memory
        p = self.tmp / "slashy"
        p.mkdir()
        memory.add_note("first", str(p))
        memory.add_note("second", str(p) + "/")
        rows = [r for r in memory.list_projects() if "slashy" in r["path"]]
        self.assertEqual(len(rows), 1, "trailing slash created a duplicate project")


if __name__ == "__main__":
    unittest.main(verbosity=2)
