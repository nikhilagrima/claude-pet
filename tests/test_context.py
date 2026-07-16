"""Phase 3 tests — context builder budget, determinism, safety block."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-test-"))
        self.db = self.tmp / "memory.sqlite"
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seed(self, project="/p", node_count=5, note_count=2, skill_count=2):
        from claude_pet import memory
        memory.record_session_start(project)
        for i in range(20):
            memory.record_tool_use("Bash" if i % 2 else "Read", project)
        memory.record_success(project)
        for i in range(note_count):
            memory.add_note(f"note {i} — remember to check foo bar", project)
        for i in range(node_count):
            memory.upsert_node(project, "convention", f"c{i}",
                              f"Convention {i}: prefer X over Y here", weight_delta=1.0 + i)
        for i in range(skill_count):
            memory.upsert_skill(f"skill-{i}", f"Skill {i}",
                               f"Reusable pattern number {i}", project, [1, 2])

    # ------------------------------------------------------- budget
    def test_output_never_exceeds_default_budget(self):
        from claude_pet import context as ctx
        self._seed(node_count=100, note_count=20, skill_count=30)
        out = ctx.build_context("/p")
        # 800 tokens × 4 chars = 3200 char ceiling. Safety block already
        # included; we allow +50 slack for the joining separators.
        self.assertLessEqual(len(out), 800 * ctx.CHARS_PER_TOKEN + 50,
                             f"output {len(out)} chars overshoots budget")

    def test_output_never_exceeds_custom_budget(self):
        from claude_pet import context as ctx
        self._seed(node_count=100, note_count=20)
        out = ctx.build_context("/p", token_budget=200)
        self.assertLessEqual(len(out), 200 * ctx.CHARS_PER_TOKEN + 50)

    # ------------------------------------------------------- safety
    def test_safety_block_always_present(self):
        from claude_pet import context as ctx
        self._seed(node_count=200, note_count=50)
        out = ctx.build_context("/p", token_budget=200)
        # Even at cramped budget, the safety line must survive.
        self.assertIn("Safety rules", out)
        self.assertIn("reuse memory", out)
        self.assertIn("security", out)

    def test_safety_block_present_when_no_history(self):
        """Fresh project with no data — safety still shows up."""
        from claude_pet import context as ctx
        out = ctx.build_context("/fresh-project")
        self.assertIn("Safety rules", out)

    # ------------------------------------------------------- determinism
    def test_deterministic_for_identical_inputs(self):
        from claude_pet import context as ctx
        self._seed()
        first = ctx.build_context("/p")
        second = ctx.build_context("/p")
        self.assertEqual(first, second, "identical DB state must produce identical output")

    # ------------------------------------------------------- ranking
    def test_higher_weight_nodes_appear_before_lower(self):
        from claude_pet import memory, context as ctx
        memory.upsert_node("/p", "decision", "lo", "LOW-weight decision")
        for _ in range(5):
            memory.upsert_node("/p", "decision", "hi", "HIGH-weight decision")
        out = ctx.build_context("/p")
        hi_pos = out.find("HIGH-weight")
        lo_pos = out.find("LOW-weight")
        self.assertGreater(hi_pos, -1)
        # Low-weight may not fit; if it does, it must come after.
        if lo_pos > -1:
            self.assertLess(hi_pos, lo_pos)

    def test_estimate_tokens_matches_chars_over_four(self):
        from claude_pet import context as ctx
        self.assertEqual(ctx.estimate_tokens(""), 0)
        self.assertEqual(ctx.estimate_tokens("a" * 100), 25)
        self.assertEqual(ctx.estimate_tokens("a" * 101), 26)  # rounds up


if __name__ == "__main__":
    unittest.main(verbosity=2)
