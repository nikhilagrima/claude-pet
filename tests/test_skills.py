"""Phase 4 tests — skill promotion, levels, slugs, frontmatter validity."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-test-"))
        self.db = self.tmp / "memory.sqlite"
        self.skills_root = self.tmp / "skills"

        db_patch = mock.patch("claude_pet.memory.db_path", return_value=self.db)
        db_patch.start()
        self.addCleanup(db_patch.stop)

        # Redirect the on-disk skill directory into the tmp tree.
        skills_patch = mock.patch(
            "claude_pet.skills._skills_dir",
            return_value=self.skills_root,
        )
        skills_patch.start()
        self.addCleanup(skills_patch.stop)
        self.skills_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------- slug + fs safety
    def test_slugify_is_deterministic_and_safe(self):
        from claude_pet.skills import _slugify
        s1 = _slugify("Use Bun, Not npm!")
        s2 = _slugify("Use Bun, Not npm!")
        self.assertEqual(s1, s2)
        # Only kebab a-z0-9
        self.assertRegex(s1, r"^[a-z0-9\-]+$")

    def test_slugify_handles_empty_or_pure_symbols(self):
        from claude_pet.skills import _slugify
        self.assertEqual(_slugify("!!!"), "unnamed")
        self.assertEqual(_slugify(""), "unnamed")

    # ------------------------------------------------------- promotion trigger
    def test_two_reinforcements_creates_skill_on_disk(self):
        from claude_pet import memory, skills
        # Below threshold — no skill yet.
        memory.upsert_node("/proj", "convention", "use-bun", "Use bun, not npm")
        promoted = skills.scan_and_promote("/proj")
        self.assertEqual(promoted, [])
        # Cross threshold — should promote.
        memory.upsert_node("/proj", "convention", "use-bun", "Use bun, not npm")
        promoted = skills.scan_and_promote("/proj")
        self.assertEqual(len(promoted), 1)
        p = promoted[0]
        self.assertEqual(p["level"], 1)
        self.assertEqual(p["tier"], "hatchling")
        # Disk file exists with valid frontmatter.
        [subdir] = list(self.skills_root.iterdir())
        skill_md = subdir / "SKILL.md"
        self.assertTrue(skill_md.exists())
        text = skill_md.read_text()
        # YAML frontmatter delimiters + required fields.
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name:", text)
        self.assertIn("description:", text)
        self.assertIn("tier: hatchling", text)
        self.assertIn("level: 1", text)

    def test_level_progression_via_log2(self):
        from claude_pet import memory, skills
        for expected_level, target in [(1, 2), (2, 4), (3, 8), (4, 16)]:
            # Reinforce the same node up to `target`.
            with memory.connect() as conn:
                conn.execute("DELETE FROM nodes")
                conn.execute("DELETE FROM skills")
            for _ in range(target):
                memory.upsert_node("/p", "convention", "test-key", "test val")
            promoted = skills.scan_and_promote("/p")
            self.assertEqual(len(promoted), 1)
            self.assertEqual(
                promoted[0]["level"], expected_level,
                f"reinforcements={target} should give level={expected_level}, got {promoted[0]['level']}"
            )

    def test_frontmatter_yaml_parses(self):
        """SKILL.md frontmatter must be valid YAML (Claude Code's parser is
        strict). We check with a minimal manual parse — no PyYAML dep."""
        from claude_pet import memory, skills
        for _ in range(2):
            memory.upsert_node("/p", "decision", "test", "A durable decision text")
        skills.scan_and_promote("/p")
        [subdir] = list(self.skills_root.iterdir())
        text = (subdir / "SKILL.md").read_text()
        # Extract frontmatter block
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(m, "no frontmatter delimiters found")
        block = m.group(1)
        # Every line inside must be `key: value` or `  key: value` (indented).
        for line in block.splitlines():
            if line.strip() == "":
                continue
            # Valid YAML lines: "key: value", "  key: value" (nested), or
            # "key:" alone (parent of a nested map, like `metadata:`).
            self.assertRegex(
                line, r"^(  )?[a-zA-Z_]+:(\s.+)?$",
                f"non-YAML line in frontmatter: {line!r}",
            )

    def test_reinforcement_updates_existing_skill(self):
        from claude_pet import memory, skills
        for _ in range(2):
            memory.upsert_node("/p", "convention", "x", "same text")
        skills.scan_and_promote("/p")
        # One more reinforcement → same slug, higher level once we hit 4.
        for _ in range(2):
            memory.upsert_node("/p", "convention", "x", "same text")
        skills.scan_and_promote("/p")
        subdirs = list(self.skills_root.iterdir())
        self.assertEqual(len(subdirs), 1, "must not spawn a duplicate skill dir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
