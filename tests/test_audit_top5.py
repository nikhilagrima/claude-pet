"""Regression tests for the audit top-5 shipped in v0.5.5.

Covers:
- errors module: log_exception writes to the rotating log
- hook: _emit_session_context emits whenever the block is non-empty
- server: write endpoints reject requests without X-Pet-Token
- github watcher: per-repo per-type cooldown suppresses duplicates
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ErrorLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-errors-"))
        # Force the errors module to write into an isolated tmp dir.
        p = mock.patch("claude_pet.errors._log_path",
                       return_value=self.tmp / "errors.log")
        p.start(); self.addCleanup(p.stop)
        # Reset configured flag so each test re-initializes with the mock.
        import claude_pet.errors as _errors
        _errors._configured = False

    def test_log_exception_writes_to_file(self):
        from claude_pet import errors
        try:
            raise ValueError("test-oopsy")
        except ValueError:
            errors.log_exception("test.label")
        contents = (self.tmp / "errors.log").read_text()
        self.assertIn("[test.label]", contents)
        self.assertIn("ValueError: test-oopsy", contents)

    def test_log_exception_never_raises(self):
        """Even if the log target is unwritable, log_exception must be silent."""
        from claude_pet import errors
        with mock.patch("claude_pet.errors._log_path",
                        return_value=Path("/does/not/exist/nope.log")):
            errors._configured = False
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                errors.log_exception("test.no-fs")   # must not raise


class HookGuardTests(unittest.TestCase):
    def setUp(self):
        self.db_file = Path(tempfile.mkdtemp()) / "memory.sqlite"
        p = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        p.start(); self.addCleanup(p.stop)

    def test_emit_session_context_fires_on_fresh_project(self):
        """Regression: old guard required sessions>=1 OR notes, dropping
        the safety-rules injection from brand-new projects — exactly where
        the safety scaffolding matters most."""
        import json
        from io import StringIO
        from claude_pet import hook, memory
        pp = memory.normalize_project_path("/tmp/fresh-project-audit-b1")
        # No sessions, no notes — pure cold start.
        with mock.patch("sys.stdout", new_callable=StringIO) as out:
            hook._emit_session_context(pp)
        printed = out.getvalue().strip()
        self.assertTrue(printed, "expected JSON payload on cold-start "
                                 "project; old guard dropped it")
        payload = json.loads(printed)
        block = payload["hookSpecificOutput"]["additionalContext"]
        # Safety rules ALWAYS appear — that's the whole point of this fix.
        self.assertIn("Safety rules", block)


class ServerAuthTests(unittest.TestCase):
    """Write endpoints (/state POST, /break POST, /shutdown) require token."""

    def setUp(self):
        # Point the server's token path at an isolated tmp dir so this
        # test can't clobber the real ~/.claude/claude-pet/server.token.
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-server-auth-"))
        p = mock.patch("claude_pet.server._TOKEN_PATH",
                       new=self.tmp / "server.token")
        p.start(); self.addCleanup(p.stop)
        # Force re-init of the module-level token.
        import claude_pet.server as srv
        srv._SERVER_TOKEN = srv._read_or_create_token()
        self.client = srv.app.test_client()
        self.token = srv._SERVER_TOKEN

    def test_state_post_without_token_returns_401(self):
        r = self.client.post("/state", json={"status": "idle"})
        self.assertEqual(r.status_code, 401)

    def test_state_post_with_token_succeeds(self):
        r = self.client.post(
            "/state", json={"status": "idle"},
            headers={"X-Pet-Token": self.token},
        )
        self.assertEqual(r.status_code, 200)

    def test_state_get_stays_open(self):
        """GET /state is observability — must remain unauthenticated."""
        r = self.client.get("/state")
        self.assertEqual(r.status_code, 200)

    def test_break_post_without_token_returns_401(self):
        r = self.client.post("/break", json={})
        self.assertEqual(r.status_code, 401)


class GithubCooldownTests(unittest.TestCase):
    """Per (watch_id, event_type) cooldown suppresses spammy reruns."""

    def setUp(self):
        self.db_file = Path(tempfile.mkdtemp()) / "memory.sqlite"
        self.cfg = Path(tempfile.mkdtemp()) / "config.json"
        p1 = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        p2 = mock.patch("claude_pet.github_watch.config._config_path",
                        return_value=self.cfg)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        from claude_pet.github_watch import watcher
        watcher.reset_state()

    def _push(self, eid):
        return {"id": eid, "type": "PushEvent",
                "actor": {"login": "x"},
                "repo": {"name": "a/b"},
                "created_at": "2026-01-01T00:00:00Z",
                "payload": {"ref": "refs/heads/main",
                            "commits": [{"sha": "x"}],
                            "distinct_size": 1}}

    def test_burst_of_pushes_alerts_only_once(self):
        """5 push events in rapid succession → 1 alert, 4 with reaction='none'."""
        from claude_pet.github_watch import storage, watcher, api
        w = storage.add_watch("a", "b")
        # Prime cursor
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = api.PollResult(200, [self._push("e0")], None, None, None, None)
            watcher.poll_one(w)
        watcher.reset_state()
        w = storage.list_watches()[0]
        # 5 new push events land in one poll
        events = [self._push(f"e{i}") for i in range(5, 0, -1)]  # newest-first
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = api.PollResult(200, events, None, None, None, None)
            watcher.poll_one(w)
        # Only ONE alert should be pending (first fresh event); the rest
        # were recorded with reaction='none' by the cooldown.
        pending = storage.pending_alerts()
        self.assertEqual(len(pending), 1,
                         f"expected 1 alert after cooldown, got {len(pending)}")
        all_events = storage.recent_events()
        # But all events are in the feed for the dashboard
        self.assertGreaterEqual(len(all_events), 5)

    def test_different_event_types_do_not_share_cooldown(self):
        """PushEvent cooldown must not suppress a following PullRequestEvent."""
        from claude_pet.github_watch import storage, watcher, api
        w = storage.add_watch("a", "b")
        push = self._push("push-1")
        pr_ev = {"id": "pr-1", "type": "PullRequestEvent",
                 "actor": {"login": "x"},
                 "repo": {"name": "a/b"},
                 "created_at": "2026-01-01T00:00:01Z",
                 "payload": {"action": "opened",
                             "pull_request": {"number": 1, "title": "wip",
                                              "html_url": "http://x"}}}
        # Prime
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = api.PollResult(200, [push], None, None, None, None)
            watcher.poll_one(w)
        watcher.reset_state()
        w = storage.list_watches()[0]
        # Both types land — both should alert
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = api.PollResult(
                200, [pr_ev, self._push("push-2")], None, None, None, None)
            watcher.poll_one(w)
        pending = storage.pending_alerts()
        types = {p["event_type"] for p in pending}
        self.assertEqual(types, {"PushEvent", "PullRequestEvent"})


if __name__ == "__main__":
    unittest.main()
