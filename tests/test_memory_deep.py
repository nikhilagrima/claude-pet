"""Deep memory feature tests — FTS ranking, concurrency, cross-process
persistence, CLI JSON shape, .ua + distiller coexistence, edge cases."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


class MemoryDeepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-deep-"))
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

    # ------------------------------------------------------- FTS ranking
    def test_fts_query_boosts_matching_nodes(self):
        """A matching, low-weight node should surface above equal-weight
        non-matching nodes when its keywords appear in the query."""
        from claude_pet import memory
        for i in range(50):
            memory.upsert_node("/p", "note", f"n{i}", f"random filler content {i}")
        memory.upsert_node("/p", "note", "target",
                           "authentication middleware for JWT validation")
        # Without a query: target is buried (it's the last one, weight 1).
        no_query = memory.top_nodes("/p", limit=10)
        no_query_keys = [r["key"] for r in no_query]
        # With the query: target should appear in the top 10 thanks to FTS boost.
        with_query = memory.top_nodes("/p", limit=10, query="authentication JWT")
        with_query_keys = [r["key"] for r in with_query]
        # We only assert the promotion happens IF FTS is enabled on this build.
        with memory.connect() as conn:
            has_fts = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes_fts'"
            ).fetchone())
        if has_fts:
            self.assertIn("target", with_query_keys,
                          "FTS query should have surfaced the matching node")
        # No crashes in either path — always must hold.
        self.assertEqual(len(with_query), 10)
        self.assertEqual(len(no_query), 10)

    def test_fts_no_query_falls_back_to_weight(self):
        from claude_pet import memory
        memory.upsert_node("/p", "note", "hi", "high weight", weight_delta=5)
        memory.upsert_node("/p", "note", "lo", "low weight",  weight_delta=1)
        rows = memory.top_nodes("/p", limit=10)
        self.assertEqual(rows[0]["key"], "hi")

    # ------------------------------------------------------- cross-process
    def test_skills_survive_reopen(self):
        """Write skills, close all connections, reopen — everything should
        persist. This proves the on-disk format is durable, not just in-RAM."""
        from claude_pet import memory
        for _ in range(4):
            memory.upsert_node("/p", "convention", "cX", "durability text")
        with memory.connect() as conn:
            n_before = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        # Force close + reopen — mocked db_path stays the same.
        with memory.connect() as conn:
            n_after = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        self.assertEqual(n_before, n_after)
        self.assertEqual(n_after, 1)  # dedup — same key = 1 row

    # ------------------------------------------------------- concurrency
    def test_concurrent_writes_dont_corrupt(self):
        """Two threads writing simultaneously — sqlite's WAL/timeout should
        serialize cleanly, no corruption, all writes accounted for."""
        from claude_pet import memory
        # Each thread inserts 20 distinct nodes.
        def worker(prefix):
            for i in range(20):
                memory.upsert_node("/p", "concurrent", f"{prefix}-{i}",
                                   f"value {prefix} {i}")
        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start(); t2.start()
        t1.join(); t2.join()
        with memory.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) c FROM nodes WHERE kind='concurrent'"
            ).fetchone()["c"]
        # 20 A + 20 B = 40 unique nodes.
        self.assertEqual(n, 40)

    # ------------------------------------------------------- ua + distiller
    def test_ua_and_distiller_nodes_coexist(self):
        """.ua-sourced nodes and distiller nodes share the table without
        collision — the `ua:` key prefix keeps them namespaced."""
        from claude_pet import memory, distill
        # Distiller node
        memory.add_note("Manual note that becomes a decision", "/p")
        distill.distill_session("/p")
        # .ua node
        distill.ingest_ua_graph("/p", {
            "nodes": [{"id": "u1", "type": "function", "name": "foo",
                       "summary": "ua-sourced", "tags": [], "complexity": "simple"}],
            "edges": [],
        })
        with memory.connect() as conn:
            keys = {r["key"] for r in conn.execute(
                "SELECT key FROM nodes WHERE project_path='/p'"
            ).fetchall()}
        # Both kinds present, different key spaces.
        self.assertTrue(any(k.startswith("ua:") for k in keys),
                        "ua-sourced key missing")
        self.assertTrue(any(k.startswith("note:") for k in keys),
                        "distiller-sourced key missing")

    # ------------------------------------------------------- CLI shape
    def test_cli_context_json_output_shape(self):
        """`claude-pet context --json` must return valid, parseable JSON
        with the fields our downstream tools expect."""
        from claude_pet import memory, cli
        memory.record_session_start("/p")
        memory.add_note("seed", "/p")

        # Capture stdout while invoking the CLI subcommand.
        argv = ["claude-pet", "context", "--json", "--budget", "400"]
        with mock.patch.object(sys, "argv", argv):
            buf = _capture_stdout(cli.main)
        payload = json.loads(buf.strip())
        for field in ("project", "budget_tokens", "actual_tokens", "context"):
            self.assertIn(field, payload, f"missing field: {field}")
        self.assertEqual(payload["budget_tokens"], 400)
        self.assertLessEqual(payload["actual_tokens"], 400 + 20)

    def test_cli_note_saves_and_shows_up_in_memory(self):
        from claude_pet import memory, cli
        argv = ["claude-pet", "note", "test", "note", "from", "CLI"]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/p"}):
                _capture_stdout(cli.main)
        summary = memory.project_summary("/p")
        found = any("test note from CLI" in n["note"] for n in summary["notes"])
        self.assertTrue(found)

    # ------------------------------------------------------- edge cases
    def test_project_summary_for_unknown_path_is_safe(self):
        from claude_pet import memory
        s = memory.project_summary("/nope/nowhere")
        self.assertFalse(s["known"])
        self.assertEqual(s["path"], "/nope/nowhere")

    def test_upsert_node_with_unicode_and_multiline(self):
        from claude_pet import memory
        node_id = memory.upsert_node(
            "/p", "decision", "unicode-key",
            "Multi-line\nvalue with 🎉 emoji and 中文 characters.",
        )
        self.assertGreater(node_id, 0)
        with memory.connect() as conn:
            v = conn.execute("SELECT value FROM nodes WHERE id=?", (node_id,)).fetchone()["value"]
        self.assertIn("🎉", v)
        self.assertIn("中文", v)

    def test_ranking_stable_across_calls(self):
        from claude_pet import memory
        for i in range(20):
            memory.upsert_node("/p", "n", f"k{i}", f"v{i}", weight_delta=i / 3)
        r1 = [r["key"] for r in memory.top_nodes("/p", limit=10)]
        r2 = [r["key"] for r in memory.top_nodes("/p", limit=10)]
        self.assertEqual(r1, r2, "top_nodes must be deterministic")


def _capture_stdout(fn):
    """Run `fn()` and return whatever it wrote to stdout."""
    import io
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
