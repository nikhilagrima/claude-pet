"""Realistic token-savings benchmark across 5 project profiles.

Compares the ranked ≤800-token injection against three baselines:
- naive_min:  dump every note verbatim
- naive_full: dump all notes + all tool_usage rows + all node summaries
- reread:     an estimate of what re-reading files would cost

Prints a summary table at the end so you can eyeball actual gains."""

from __future__ import annotations

import io
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SUMMARY = []


class TokenSavingsBenchmark(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-bench-"))
        self.db = self.tmp / "memory.sqlite"
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Seed a deterministic RNG so runs are reproducible.
        random.seed(42)

    # ------------------------------------------------------- fixtures
    def _seed(self, project: str, nodes: int, sessions: int, notes: int,
              tool_calls: int):
        from claude_pet import memory
        for _ in range(sessions):
            memory.record_session_start(project)
        tools = ["Read", "Bash", "Edit", "Grep", "Write", "Glob"]
        for _ in range(tool_calls):
            memory.record_tool_use(random.choice(tools), project)
        for i in range(notes):
            memory.add_note(f"note {i}: remember to keep the {random.choice(['auth','api','db','ui'])} module clean and test-covered", project)
        kinds = ["decision", "convention", "fix", "gotcha", "file", "function"]
        # Bias distribution so ranked-injection has real signal to choose from.
        for i in range(nodes):
            k = kinds[i % len(kinds)]
            weight = 1 + (i % 5)  # 1-5 to spread ranking
            for _ in range(weight):
                memory.upsert_node(
                    project, k, f"{k}-{i}",
                    f"{k.title()} {i}: prefer approach X over Y because reason Z in the {random.choice(['auth','api','db','ui'])} layer",
                    file_path=f"src/{k}_{i}.py" if i % 3 == 0 else None,
                )
        memory.record_success(project)

    # ------------------------------------------------------- baselines
    def _naive_min(self, project: str) -> str:
        from claude_pet import memory
        with memory.connect() as conn:
            notes = conn.execute(
                "SELECT note FROM notes WHERE project_path=?", (project,)
            ).fetchall()
        return "\n".join(n["note"] for n in notes)

    def _naive_full(self, project: str) -> str:
        from claude_pet import memory
        with memory.connect() as conn:
            notes = conn.execute(
                "SELECT note FROM notes WHERE project_path=?", (project,)
            ).fetchall()
            tools = conn.execute(
                "SELECT tool_name, count FROM tool_usage WHERE project_path=?", (project,)
            ).fetchall()
            nodes = conn.execute(
                "SELECT kind, value FROM nodes WHERE project_path=?", (project,)
            ).fetchall()
        parts = []
        parts.extend(n["note"] for n in notes)
        parts.extend(f"{t['tool_name']} used {t['count']} times" for t in tools)
        parts.extend(f"[{n['kind']}] {n['value']}" for n in nodes)
        return "\n".join(parts)

    def _reread_estimate(self, project: str) -> int:
        """Conservative estimate of tokens Claude would spend re-reading
        every file mentioned in the graph. ~800 tokens per source file."""
        from claude_pet import memory
        with memory.connect() as conn:
            files = conn.execute(
                "SELECT DISTINCT file_path FROM nodes WHERE project_path=? AND file_path IS NOT NULL",
                (project,),
            ).fetchall()
        return len(files) * 800

    # ------------------------------------------------------- scenarios
    def _run(self, name: str, **seed_args):
        from claude_pet import context as ctx
        project = f"/tmp/{name}"
        if any(v for v in seed_args.values()):
            self._seed(project, **seed_args)
        injected = ctx.build_context(project)
        naive_min = self._naive_min(project)
        naive_full = self._naive_full(project)
        reread = self._reread_estimate(project)

        row = {
            "scenario":   name,
            "injected":   (len(injected), ctx.estimate_tokens(injected)),
            "naive_min":  (len(naive_min), ctx.estimate_tokens(naive_min)),
            "naive_full": (len(naive_full), ctx.estimate_tokens(naive_full)),
            "reread_est": reread,
        }
        SUMMARY.append(row)
        # Universal invariants.
        self.assertIn("Safety rules", injected, f"{name}: safety block missing")
        self.assertLessEqual(
            ctx.estimate_tokens(injected), 800 + 20,
            f"{name}: injection exceeded 800-token budget",
        )
        return row

    def test_empty_project(self):
        self._run("empty", nodes=0, sessions=0, notes=0, tool_calls=0)

    def test_small_project(self):
        self._run("small", nodes=5, sessions=2, notes=2, tool_calls=15)

    def test_medium_project(self):
        self._run("medium", nodes=30, sessions=8, notes=10, tool_calls=80)

    def test_large_project(self):
        self._run("large", nodes=200, sessions=20, notes=40, tool_calls=500)

    def test_heavy_history(self):
        self._run("heavy_history", nodes=500, sessions=50, notes=100, tool_calls=2000)


def print_summary_and_exit():
    from claude_pet import context as ctx
    print("\n" + "=" * 82)
    print("REAL TOKEN-SAVINGS BENCHMARK  (chars / ≈tokens)")
    print("=" * 82)
    print(f"{'scenario':<15} {'injected':<18} {'naive_min':<18} {'naive_full':<18} {'reread_est':>10}")
    print("-" * 82)
    for r in SUMMARY:
        inj = f"{r['injected'][0]:>5} / {r['injected'][1]:<4}"
        nmin = f"{r['naive_min'][0]:>5} / {r['naive_min'][1]:<4}"
        nfull = f"{r['naive_full'][0]:>5} / {r['naive_full'][1]:<4}"
        print(f"{r['scenario']:<15} {inj:<18} {nmin:<18} {nfull:<18} {r['reread_est']:>10}")
    print("=" * 82)
    print("Interpretation:")
    print("  * `injected` = what the pet actually sends to Claude on session start")
    print("  * `naive_min` / `naive_full` = what a dumb dump would cost")
    print("  * `reread_est` = tokens Claude would spend re-reading files without memory")
    print("  * On large projects the pet's ranked block stays under budget while")
    print("    a full dump grows unbounded — that gap is the real savings.")
    print("=" * 82 + "\n")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2)
    print_summary_and_exit()
    sys.exit(0 if result.result.wasSuccessful() else 1)
