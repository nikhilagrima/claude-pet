"""Regression tests for:
- distill_session now writes co-occurred edges between session nodes
- StatsTab token-savings estimate reflects real context-block size × sessions
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _isolated_db():
    return Path(tempfile.mkdtemp(prefix="claude-pet-graph-")) / "memory.sqlite"


class EdgeCoOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.db_file = _isolated_db()
        p = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        p.start(); self.addCleanup(p.stop)

    def test_two_nodes_written_together_get_one_edge(self):
        """distill_session must connect its own outputs, not just leave a
        cloud of disconnected dots."""
        from claude_pet import memory, distill
        pp = "/tmp/edgetest"

        # Seed enough state that distill_session produces at least 2 nodes.
        memory.record_session_start(pp)
        for _ in range(5):
            memory.record_tool_use("Bash", pp)   # dominant tool → convention node
        memory.add_note("please use bunx not npx", pp)  # → decision node

        # First distill: creates convention + decision, links them.
        written = distill.distill_session(pp)
        self.assertGreaterEqual(len(written), 2)

        with memory.connect() as c:
            edges = c.execute(
                "SELECT src_id, dst_id, kind, weight FROM edges WHERE project_path=?",
                (memory.normalize_project_path(pp),),
            ).fetchall()
        self.assertGreaterEqual(len(edges), 1)
        self.assertTrue(any(e["kind"] == "co-occurred" for e in edges))

    def test_repeat_distill_bumps_edge_weight_idempotently(self):
        from claude_pet import memory, distill
        pp = "/tmp/edgereinforce"
        memory.record_session_start(pp)
        for _ in range(5):
            memory.record_tool_use("Bash", pp)
        memory.add_note("same convention keeps reappearing", pp)

        distill.distill_session(pp)
        distill.distill_session(pp)   # same session state → same node pair
        with memory.connect() as c:
            rows = c.execute(
                "SELECT weight FROM edges WHERE project_path=? AND kind='co-occurred'",
                (memory.normalize_project_path(pp),),
            ).fetchall()
        self.assertEqual(len(rows), 1, "should upsert, not duplicate")
        self.assertGreater(rows[0]["weight"], 0.5,
                           "repeat call must reinforce the edge")


class TokenSavingsEstimateTests(unittest.TestCase):
    def setUp(self):
        self.db_file = _isolated_db()
        p = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        p.start(); self.addCleanup(p.stop)

    def _stats_estimate(self) -> int:
        """Recreate the panel's formula without needing Qt."""
        from claude_pet import memory
        with memory.connect() as conn:
            n_nodes = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
            per_project_counts = [
                r["session_count"] for r in conn.execute(
                    "SELECT session_count FROM projects"
                ).fetchall()
            ]
        per_block_tokens = 0
        projects = memory.list_projects(limit=1)
        if projects:
            from claude_pet import context as ctx_mod
            per_block_tokens = max(0, len(ctx_mod.build_context(
                projects[0]["path"], token_budget=800)) // 4)
        sessions_with_memory = sum(max(0, c - 1) for c in per_project_counts)
        return sessions_with_memory * per_block_tokens + n_nodes * 40

    def test_empty_db_zero_savings(self):
        self.assertEqual(self._stats_estimate(), 0)

    def test_one_session_one_project_no_prior_memory_still_gets_node_baseline(self):
        from claude_pet import memory
        pp = "/tmp/tokproj"
        memory.record_session_start(pp)
        memory.upsert_node(pp, "decision", "k1", "some decision")
        # Only 1 session, 1 project → sessions_with_memory = 0. Node baseline only.
        est = self._stats_estimate()
        self.assertEqual(est, 40)     # 1 node × 40

    def test_mixed_project_counts_do_not_wrongly_subtract(self):
        """Regression: real-user DB had 3 sessions, 4 projects (2 with 0
        sessions). Old formula (n_sessions - n_projects) gave -1 → clamped to
        0, hiding real savings. Correct formula sums max(0, count-1) per
        project — should give ≥ 1 when at least one project has ≥ 2 sessions.
        """
        from claude_pet import memory
        # Mix: one project with 2 real sessions, one with 1, two with 0 (auto-
        # registered by hooks but never produced a session).
        pp_active = "/tmp/active"
        pp_solo = "/tmp/solo"
        memory.record_session_start(pp_active)
        memory.record_session_start(pp_active)   # 2nd session → memory injected
        memory.record_session_start(pp_solo)
        memory.upsert_node(pp_active, "decision", "k1", "a decision")
        # Two "cold" projects with zero real sessions.
        with memory.connect() as c:
            c.execute("INSERT INTO projects (path, name, first_seen, last_seen, session_count) VALUES ('/tmp/cold1', 'c1', 't', 't', 0)")
            c.execute("INSERT INTO projects (path, name, first_seen, last_seen, session_count) VALUES ('/tmp/cold2', 'c2', 't', 't', 0)")
        est = self._stats_estimate()
        self.assertGreater(est, 40,
                           f"expected some cumulative savings from the active "
                           f"project's 2nd session, got {est}")

    def test_repeat_sessions_scale_savings_upward(self):
        """N sessions of the same project should scale token savings linearly
        with the actual context block size, not stay flat at n_nodes × 40."""
        from claude_pet import memory
        pp = "/tmp/tokproj2"
        for _ in range(10):
            memory.record_session_start(pp)
        # Add several nodes so build_context has real content.
        for i in range(5):
            memory.upsert_node(pp, "decision", f"k{i}", f"decision number {i}")
        est = self._stats_estimate()
        # sessions_with_memory = 10 - 1 = 9. Even a 100-token block gives 900+.
        self.assertGreater(est, 500,
                           f"expected substantial cumulative estimate, got {est}")


if __name__ == "__main__":
    unittest.main()
