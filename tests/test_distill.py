"""Phase 2 tests — distiller, secret redaction, .ua ingestion."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DistillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-test-"))
        self.db = self.tmp / "memory.sqlite"
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Isolate each test — fresh DB.
        from claude_pet import memory
        with memory.connect():
            pass

    # ---------------------------------------------------------------- redact
    def test_redact_scrubs_common_secret_shapes(self):
        from claude_pet.distill import redact
        cases = [
            "AKIAABCDEFGHIJKLMNOP",
            "ghp_" + "a" * 40,
            "sk-ant-" + "x" * 40,
            "sk-" + "x" * 40,
            "xoxb-1-abcdefghij",
            "AIzaSy" + "x" * 33,
            "sk_live_" + "x" * 30,
            "eyJhbGciOi.eyJzdWIiOi.SflKxwRJSMe",
            "Authorization: Bearer abcdef0123456789abcdef",
            "api_key='mysecretkeyvalueherexxx1234'",
        ]
        for c in cases:
            self.assertIn("[REDACTED", redact(c), f"leaked: {c}")

    def test_redact_leaves_ordinary_text_alone(self):
        from claude_pet.distill import redact
        ordinary = "Use bun instead of npm for this project's install commands."
        self.assertEqual(redact(ordinary), ordinary)

    def test_redact_is_idempotent(self):
        from claude_pet.distill import redact
        once = redact("ghp_" + "z" * 40)
        twice = redact(once)
        self.assertEqual(once, twice)

    # ---------------------------------------------------------------- upsert
    def test_same_fact_twice_bumps_weight_not_row_count(self):
        from claude_pet import memory
        # Seed enough tool_usage to trigger the dominance convention.
        for _ in range(10):
            memory.record_tool_use("Bash", "/p")
        from claude_pet.distill import distill_session
        distill_session("/p")
        distill_session("/p")   # second Stop, same session
        with memory.connect() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM nodes WHERE project_path='/p' AND kind='convention'").fetchone()["c"]
            row = conn.execute("SELECT weight, reinforcements FROM nodes WHERE project_path='/p' AND kind='convention'").fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(row["reinforcements"], 2)
        self.assertGreater(row["weight"], 1.0)

    def test_notes_become_decision_nodes(self):
        from claude_pet import memory
        from claude_pet.distill import distill_session
        memory.add_note("Working on the auth refactor", "/p")
        memory.add_note("Ship before Friday", "/p")
        distill_session("/p")
        with memory.connect() as conn:
            rows = conn.execute(
                "SELECT value FROM nodes WHERE project_path='/p' AND kind='decision'"
            ).fetchall()
        values = [r["value"] for r in rows]
        self.assertTrue(any("auth refactor" in v for v in values))
        self.assertTrue(any("Ship before Friday" in v for v in values))

    def test_secrets_never_reach_the_db(self):
        from claude_pet import memory
        from claude_pet.distill import distill_session
        memory.add_note("api_key='sk-abcdefghijklmnop123456xyz'", "/p")
        distill_session("/p")
        with memory.connect() as conn:
            rows = conn.execute("SELECT value FROM nodes WHERE project_path='/p'").fetchall()
        for r in rows:
            self.assertNotIn("sk-abcdefghijklmnop", r["value"])
            self.assertIn("[REDACTED", r["value"])

    # -------------------------------------------------------------- .ua ingest
    def test_ua_graph_ingestion_losslessly_upserts_nodes(self):
        graph = {
            "version": "1.0",
            "project": {"name": "demo", "languages": ["ts"], "frameworks": [],
                        "description": "d", "analyzedAt": "now", "gitCommitHash": "x"},
            "nodes": [
                {"id": "n1", "type": "file", "name": "auth.ts",
                 "filePath": "src/auth.ts",
                 "summary": "Handles JWT auth",
                 "tags": [], "complexity": "moderate"},
                {"id": "n2", "type": "function", "name": "verifyToken",
                 "filePath": "src/auth.ts", "summary": "Verifies a JWT.",
                 "tags": [], "complexity": "simple"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "type": "contains",
                 "direction": "forward", "weight": 0.9},
            ],
            "layers": [], "tour": [],
        }
        from claude_pet.distill import ingest_ua_graph
        n = ingest_ua_graph("/p", graph)
        self.assertEqual(n, 2)
        from claude_pet import memory
        with memory.connect() as conn:
            nodes = conn.execute("SELECT kind, key, value, file_path FROM nodes WHERE project_path='/p'").fetchall()
            edges = conn.execute("SELECT src_id, dst_id, kind FROM edges WHERE project_path='/p'").fetchall()
        kinds = {r["kind"] for r in nodes}
        self.assertEqual(kinds, {"file", "function"})
        self.assertTrue(any(r["file_path"] == "src/auth.ts" for r in nodes))
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["kind"], "contains")

    def test_ua_ingest_dedups_across_calls(self):
        graph = {
            "nodes": [{"id": "n1", "type": "file", "name": "a", "summary": "a",
                       "tags": [], "complexity": "simple"}],
            "edges": [],
        }
        from claude_pet.distill import ingest_ua_graph
        ingest_ua_graph("/p", graph)
        ingest_ua_graph("/p", graph)
        from claude_pet import memory
        with memory.connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM nodes WHERE project_path='/p'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_ua_dir_detection(self):
        """`.ua/knowledge-graph.json` inside a project is auto-picked up."""
        proj = self.tmp / "proj-with-ua"
        (proj / ".ua").mkdir(parents=True)
        (proj / ".ua" / "knowledge-graph.json").write_text(json.dumps({
            "nodes": [{"id": "x", "type": "concept", "name": "seed",
                       "summary": "the seed", "tags": [], "complexity": "simple"}],
            "edges": [],
        }))
        from claude_pet.distill import ingest_ua_dir_if_present
        n = ingest_ua_dir_if_present(str(proj))
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
