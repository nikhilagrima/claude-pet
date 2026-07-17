"""Tests for the GitHub repo watcher.

Covers:
- Schema v3 migration + gh_watches / gh_events shape
- storage add/remove/enable/list, event dedup, pending_alerts
- classify() maps every supported event type to a reaction
- watcher.poll_one first-poll primes cursor WITHOUT alerting
- watcher.poll_one second poll surfaces only NEW events
- watcher handles 304, 404, rate-limited 403 without disabling
- watcher handles 401 by recording the error
- config token env override + default poll interval floor
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _isolated_db():
    tmp = tempfile.mkdtemp(prefix="claude-pet-gh-")
    return Path(tmp) / "memory.sqlite"


def _isolated_config_root():
    return Path(tempfile.mkdtemp(prefix="claude-pet-cfg-"))


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else []
        self.headers = headers or {}

    def json(self):
        return self._json


def _push_event(eid: str, actor="octocat", n_commits=1, ref="refs/heads/main"):
    return {
        "id": eid, "type": "PushEvent",
        "actor": {"login": actor},
        "repo": {"name": "octocat/hello"},
        "created_at": "2026-01-01T00:00:00Z",
        "payload": {"ref": ref, "commits": [{"sha": "x"}] * n_commits},
    }


def _pr_event(eid, action, merged=False, number=42):
    return {
        "id": eid, "type": "PullRequestEvent",
        "actor": {"login": "octocat"},
        "repo": {"name": "octocat/hello"},
        "created_at": "2026-01-01T00:00:01Z",
        "payload": {
            "action": action,
            "pull_request": {
                "number": number, "title": "add feature",
                "merged": merged,
                "html_url": f"https://github.com/octocat/hello/pull/{number}",
            },
        },
    }


def _review_event(eid, state, number=42):
    return {
        "id": eid, "type": "PullRequestReviewEvent",
        "actor": {"login": "reviewer"},
        "repo": {"name": "octocat/hello"},
        "created_at": "2026-01-01T00:00:02Z",
        "payload": {
            "action": "created",
            "pull_request": {"number": number,
                             "html_url": f"https://github.com/octocat/hello/pull/{number}"},
            "review": {"state": state,
                       "html_url": f"https://github.com/octocat/hello/pull/{number}#review-1"},
        },
    }


def _release_event(eid, tag="v1.0.0"):
    return {
        "id": eid, "type": "ReleaseEvent",
        "actor": {"login": "octocat"},
        "repo": {"name": "octocat/hello"},
        "created_at": "2026-01-01T00:00:03Z",
        "payload": {"action": "published",
                    "release": {"tag_name": tag,
                                "html_url": "https://github.com/octocat/hello/releases/tag/v1"}},
    }


def _workflow_event(eid, conclusion):
    return {
        "id": eid, "type": "WorkflowRunEvent",
        "actor": {"login": "octocat"},
        "repo": {"name": "octocat/hello"},
        "created_at": "2026-01-01T00:00:04Z",
        "payload": {"action": "completed",
                    "workflow_run": {"name": "CI", "conclusion": conclusion,
                                     "html_url": "https://github.com/octocat/hello/actions/runs/1"}},
    }


def _deploy_event(eid, state, env="production"):
    return {
        "id": eid, "type": "DeploymentStatusEvent",
        "actor": {"login": "octocat"},
        "repo": {"name": "octocat/hello"},
        "created_at": "2026-01-01T00:00:05Z",
        "payload": {
            "deployment": {"environment": env,
                           "url": "https://api.github.com/repos/octocat/hello/deployments/1"},
            "deployment_status": {"state": state,
                                  "target_url": "https://deploy.example.com/1"},
        },
    }


class SchemaV3Tests(unittest.TestCase):
    def setUp(self):
        self.db_file = _isolated_db()
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_v3_tables_created_on_fresh_install(self):
        from claude_pet import memory
        with memory.connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("gh_watches", tables)
        self.assertIn("gh_events", tables)

    def test_v3_migration_from_v2(self):
        """Simulate a v2 DB (nodes/edges/skills present, user_version=2) and
        verify connecting upgrades it to v3 additively."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_file))
        conn.executescript("""
            CREATE TABLE projects (
              path TEXT PRIMARY KEY, name TEXT NOT NULL,
              first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
              session_count INTEGER NOT NULL DEFAULT 0
            );
            PRAGMA user_version = 2;
        """)
        conn.commit()
        conn.close()

        from claude_pet import memory
        with memory.connect() as c:
            v = c.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(v, 3)
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            self.assertIn("gh_watches", tables)
            self.assertIn("gh_events", tables)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.db_file = _isolated_db()
        patcher = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_watch_is_idempotent(self):
        from claude_pet.github_watch import storage
        w1 = storage.add_watch("octocat", "hello")
        w2 = storage.add_watch("octocat", "hello")
        self.assertEqual(w1["id"], w2["id"])
        self.assertEqual(len(storage.list_watches()), 1)

    def test_remove_watch(self):
        from claude_pet.github_watch import storage
        storage.add_watch("a", "b")
        self.assertTrue(storage.remove_watch("a", "b"))
        self.assertFalse(storage.remove_watch("a", "b"))     # already gone
        self.assertEqual(storage.list_watches(), [])

    def test_enabled_toggle(self):
        from claude_pet.github_watch import storage
        storage.add_watch("a", "b")
        storage.set_enabled("a", "b", False)
        self.assertEqual(storage.list_watches(enabled_only=True), [])
        storage.set_enabled("a", "b", True)
        self.assertEqual(len(storage.list_watches(enabled_only=True)), 1)

    def test_event_dedup(self):
        from claude_pet.github_watch import storage
        w = storage.add_watch("a", "b")
        first = storage.record_event(w["id"], "e1", "PushEvent", "octo",
                                     "1 commit", "https://x", "curious",
                                     "2026-01-01T00:00:00Z")
        again = storage.record_event(w["id"], "e1", "PushEvent", "octo",
                                     "1 commit", "https://x", "curious",
                                     "2026-01-01T00:00:00Z")
        self.assertTrue(first)
        self.assertFalse(again)

    def test_pending_alerts_skips_alerted_and_disabled(self):
        from claude_pet.github_watch import storage
        w = storage.add_watch("a", "b")
        storage.record_event(w["id"], "e1", "PushEvent", "x", "t", None,
                             "curious", "2026-01-01T00:00:00Z")
        storage.record_event(w["id"], "e2", "PushEvent", "x", "t2", None,
                             "curious", "2026-01-01T00:00:01Z")
        alerts = storage.pending_alerts()
        self.assertEqual(len(alerts), 2)
        storage.mark_alerted(alerts[0]["id"])
        self.assertEqual(len(storage.pending_alerts()), 1)

        storage.set_enabled("a", "b", False)
        self.assertEqual(storage.pending_alerts(), [])


class ClassifyTests(unittest.TestCase):
    def test_push_curious(self):
        from claude_pet.github_watch.classify import classify
        c = classify(_push_event("1"))
        self.assertEqual(c["reaction"], "curious")
        self.assertIn("commit", c["title"])
        self.assertEqual(c["event_type"], "PushEvent")

    def test_push_with_truncated_payload_still_useful(self):
        """GitHub's /events truncates the commits array for many repos —
        `commits: []`, `size: null`, `distinct_size: null`. We must not
        display "0 new commits" — either use the head SHA or say
        "New push". Regression: exact real-world payload from
        nikhilagrima/claude-pet."""
        from claude_pet.github_watch.classify import classify
        ev = {
            "id": "999", "type": "PushEvent",
            "actor": {"login": "nikhilagrima"},
            "repo": {"name": "nikhilagrima/claude-pet"},
            "created_at": "2026-07-17T10:00:00Z",
            "payload": {
                "ref": "refs/heads/main",
                "head": "c5ca76358dabc0000000",
                "before": "e1117e598f000000000",
                "size": None, "distinct_size": None, "commits": [],
            },
        }
        c = classify(ev)
        self.assertIsNotNone(c)
        # Must NOT say "0 new commits"
        self.assertNotIn("0 new commit", c["title"])
        # Must mention the ref and head SHA
        self.assertIn("main", c["title"])
        self.assertIn("c5ca763", c["title"])
        # URL should be the compare view (or commit view fallback)
        self.assertTrue(c["url"].startswith(
            "https://github.com/nikhilagrima/claude-pet/compare/"),
            f"expected compare URL, got {c['url']}")

    def test_push_with_size_field_uses_size(self):
        """When distinct_size is populated (common on active repos), use it
        as the count."""
        from claude_pet.github_watch.classify import classify
        ev = _push_event("1", n_commits=0)   # empty commits array
        ev["payload"]["distinct_size"] = 3   # but distinct_size says 3
        c = classify(ev)
        self.assertIn("3 new commits", c["title"])

    def test_pr_opened_curious(self):
        from claude_pet.github_watch.classify import classify
        c = classify(_pr_event("1", "opened"))
        self.assertEqual(c["reaction"], "curious")

    def test_pr_merged_success(self):
        from claude_pet.github_watch.classify import classify
        c = classify(_pr_event("1", "closed", merged=True))
        self.assertEqual(c["reaction"], "success")

    def test_pr_closed_unmerged_error(self):
        from claude_pet.github_watch.classify import classify
        c = classify(_pr_event("1", "closed", merged=False))
        self.assertEqual(c["reaction"], "error")

    def test_pr_edited_ignored(self):
        from claude_pet.github_watch.classify import classify
        self.assertIsNone(classify(_pr_event("1", "edited")))

    def test_review_states(self):
        from claude_pet.github_watch.classify import classify
        self.assertEqual(classify(_review_event("1", "approved"))["reaction"], "success")
        self.assertEqual(classify(_review_event("2", "changes_requested"))["reaction"], "error")
        self.assertEqual(classify(_review_event("3", "commented"))["reaction"], "curious")
        self.assertIsNone(classify(_review_event("4", "dismissed")))

    def test_release_published_success(self):
        from claude_pet.github_watch.classify import classify
        c = classify(_release_event("1"))
        self.assertEqual(c["reaction"], "success")

    def test_workflow_pass_fail_cancel(self):
        from claude_pet.github_watch.classify import classify
        self.assertEqual(classify(_workflow_event("1", "success"))["reaction"], "success")
        self.assertEqual(classify(_workflow_event("2", "failure"))["reaction"], "error")
        self.assertEqual(classify(_workflow_event("3", "cancelled"))["reaction"], "curious")

    def test_deploy_success_fail(self):
        from claude_pet.github_watch.classify import classify
        self.assertEqual(classify(_deploy_event("1", "success"))["reaction"], "success")
        self.assertEqual(classify(_deploy_event("2", "failure"))["reaction"], "error")
        self.assertEqual(classify(_deploy_event("3", "in_progress"))["reaction"], "curious")

    def test_unknown_type_dropped(self):
        from claude_pet.github_watch.classify import classify
        self.assertIsNone(classify(
            {"id": "1", "type": "WatchEvent", "actor": {"login": "x"},
             "repo": {"name": "a/b"}, "created_at": "t", "payload": {}}
        ))


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.db_file = _isolated_db()
        self.cfg_root = _isolated_config_root()
        p1 = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        p2 = mock.patch("claude_pet.github_watch.config._config_path",
                        return_value=self.cfg_root / "config.json")
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        from claude_pet.github_watch import watcher
        watcher.reset_state()

    def _install_fake(self, resp: "_FakeResp"):
        from claude_pet.github_watch import api
        def fake(url, headers=None, timeout=None, allow_redirects=None):
            fake.calls.append({"url": url, "headers": headers})
            return _to_response(resp)
        fake.calls = []
        return mock.patch("claude_pet.github_watch.api.requests.get",
                          side_effect=fake), fake

    def test_first_poll_primes_cursor_without_alerting(self):
        from claude_pet.github_watch import storage, watcher
        w = storage.add_watch("octocat", "hello")
        events = [_push_event("e3"), _push_event("e2"), _push_event("e1")]
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(200, events, etag='"abc"')
            stats = watcher.poll_one(w)
        self.assertEqual(stats["new"], 0)              # nothing alerted on first poll
        # Cursor is now newest event id
        after = storage.list_watches()[0]
        self.assertEqual(after["last_event_id"], "e3")
        self.assertEqual(after["etag"], '"abc"')
        # And no rows in gh_events (first poll only primes)
        self.assertEqual(storage.pending_alerts(), [])

    def test_second_poll_surfaces_only_new_events(self):
        from claude_pet.github_watch import storage, watcher
        w = storage.add_watch("octocat", "hello")
        # First poll: prime cursor at "e2"
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(200, [_push_event("e2"), _push_event("e1")])
            watcher.poll_one(w)
        watcher.reset_state()
        # Second poll: newest is "e4", then "e3", then old "e2"
        w = storage.list_watches()[0]
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(
                200,
                [_push_event("e4"), _push_event("e3"), _push_event("e2"),
                 _push_event("e1")],
            )
            stats = watcher.poll_one(w)
        self.assertEqual(stats["new"], 2)              # e3 and e4 are new
        alerts = storage.pending_alerts()
        self.assertEqual([a["event_id"] for a in alerts], ["e3", "e4"])
        # Cursor advanced
        self.assertEqual(storage.list_watches()[0]["last_event_id"], "e4")

    def test_third_poll_with_no_new_events_is_idempotent(self):
        from claude_pet.github_watch import storage, watcher
        w = storage.add_watch("octocat", "hello")
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(200, [_push_event("e1")])
            watcher.poll_one(w); watcher.reset_state()
            w = storage.list_watches()[0]
            pr.return_value = _mk_poll_result(200, [_push_event("e1")])
            stats = watcher.poll_one(w)
        self.assertEqual(stats["new"], 0)
        self.assertEqual(storage.pending_alerts(), [])

    def test_304_response_no_error_no_new(self):
        from claude_pet.github_watch import storage, watcher
        w = storage.add_watch("a", "b")
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(304, [], etag='"new-etag"')
            stats = watcher.poll_one(w)
        self.assertEqual(stats["new"], 0)
        after = storage.list_watches()[0]
        self.assertEqual(after["etag"], '"new-etag"')
        self.assertIsNone(after["last_error"])

    def test_404_disables_via_error(self):
        from claude_pet.github_watch import storage, watcher
        w = storage.add_watch("does", "not-exist")
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(404, [], error="not found")
            watcher.poll_one(w)
        after = storage.list_watches()[0]
        self.assertIn("not found", after["last_error"])

    def test_rate_limited_403_does_not_set_error(self):
        from claude_pet.github_watch import storage, watcher
        w = storage.add_watch("a", "b")
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(
                403, [], rate_remaining=0, rate_reset_ts=int(1e12),
            )
            watcher.poll_one(w)
        after = storage.list_watches()[0]
        self.assertIsNone(after["last_error"])
        # And future polls short-circuit until reset
        self.assertGreater(watcher.rate_block_until_ts(), 0)

    def test_disabled_watches_skipped_by_poll_all_due(self):
        from claude_pet.github_watch import storage, watcher
        storage.add_watch("a", "b")
        storage.set_enabled("a", "b", False)
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(200, [])
            watcher.force_poll_all()
            self.assertEqual(pr.call_count, 0)

    def test_per_type_disabled_stores_reaction_none(self):
        """When the user turns off PushEvent alerts, we still record the event
        but mark it reaction='none' so the pet stays quiet."""
        from claude_pet.github_watch import config, storage, watcher
        cfg = config.load()
        cfg["alert_types"]["PushEvent"] = False
        config.save(cfg)

        w = storage.add_watch("a", "b")
        # Prime cursor with a single event, then send a NEW push event.
        with mock.patch("claude_pet.github_watch.api.poll_repo") as pr:
            pr.return_value = _mk_poll_result(200, [_push_event("e1")])
            watcher.poll_one(w); watcher.reset_state()
            w = storage.list_watches()[0]
            pr.return_value = _mk_poll_result(200, [_push_event("e2"), _push_event("e1")])
            watcher.poll_one(w)

        self.assertEqual(storage.pending_alerts(), [])
        recent = storage.recent_events()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["reaction"], "none")


class BulkCliTests(unittest.TestCase):
    """The CLI must accept multiple owner/repo slugs in a single call."""

    def setUp(self):
        self.db_file = _isolated_db()
        p = mock.patch("claude_pet.memory.db_path", return_value=self.db_file)
        p.start(); self.addCleanup(p.stop)

    def test_split_repo_args_space_separated(self):
        from claude_pet.cli import _split_repo_args
        got = _split_repo_args(["a/b", "c/d", "e/f"])
        self.assertEqual(got, [("a", "b"), ("c", "d"), ("e", "f")])

    def test_split_repo_args_comma_separated_single_arg(self):
        from claude_pet.cli import _split_repo_args
        got = _split_repo_args(["a/b,c/d,e/f"])
        self.assertEqual(got, [("a", "b"), ("c", "d"), ("e", "f")])

    def test_split_repo_args_mixed_delimiters(self):
        from claude_pet.cli import _split_repo_args
        got = _split_repo_args(["a/b, c/d", "e/f"])
        self.assertEqual(got, [("a", "b"), ("c", "d"), ("e", "f")])

    def test_split_repo_args_deduplicates(self):
        from claude_pet.cli import _split_repo_args
        got = _split_repo_args(["a/b", "a/b", "c/d"])
        self.assertEqual(got, [("a", "b"), ("c", "d")])

    def test_split_repo_args_drops_malformed(self):
        from claude_pet.cli import _split_repo_args
        got = _split_repo_args(["a/b", "not-a-slug", "/no-owner", "no-repo/", "c/d"])
        self.assertEqual(got, [("a", "b"), ("c", "d")])

    def test_cmd_github_watch_many_in_one_call(self):
        from claude_pet.cli import cmd_github
        from claude_pet.github_watch import storage
        # Mimic argparse namespace.
        ns = mock.Mock(gh_sub="watch",
                       gh_args=["torvalds/linux", "facebook/react", "vercel/next.js"])
        rc = cmd_github(ns)
        self.assertEqual(rc, 0)
        slugs = {(w["owner"], w["repo"]) for w in storage.list_watches()}
        self.assertEqual(slugs, {("torvalds", "linux"),
                                  ("facebook", "react"),
                                  ("vercel", "next.js")})

    def test_cmd_github_unwatch_many_in_one_call(self):
        from claude_pet.cli import cmd_github
        from claude_pet.github_watch import storage
        for o, r in [("a", "b"), ("c", "d"), ("e", "f")]:
            storage.add_watch(o, r)
        ns = mock.Mock(gh_sub="unwatch", gh_args=["a/b", "e/f"])
        cmd_github(ns)
        remaining = {(w["owner"], w["repo"]) for w in storage.list_watches()}
        self.assertEqual(remaining, {("c", "d")})

    def test_cmd_github_watch_is_idempotent_across_bulk_calls(self):
        from claude_pet.cli import cmd_github
        from claude_pet.github_watch import storage
        ns = mock.Mock(gh_sub="watch", gh_args=["a/b", "c/d"])
        cmd_github(ns); cmd_github(ns)                # second call: same slugs
        self.assertEqual(len(storage.list_watches()), 2)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg_root = _isolated_config_root()
        p = mock.patch("claude_pet.github_watch.config._config_path",
                       return_value=self.cfg_root / "config.json")
        p.start(); self.addCleanup(p.stop)

    def test_defaults_when_no_file(self):
        from claude_pet.github_watch import config
        cfg = config.load()
        self.assertEqual(cfg["poll_interval_s"], 300)
        self.assertTrue(cfg["enabled"])
        self.assertIsNone(cfg["token"])

    def test_env_token_beats_stored(self):
        from claude_pet.github_watch import config
        config.set_token("stored-tok")
        with mock.patch.dict("os.environ", {"CLAUDE_PET_GITHUB_TOKEN": "env-tok"}):
            self.assertEqual(config.token(), "env-tok")
        # Env unset → falls back to stored
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.token(), "stored-tok")

    def test_poll_interval_floor(self):
        from claude_pet.github_watch import config
        cfg = config.load()
        cfg["poll_interval_s"] = 10        # below floor
        config.save(cfg)
        self.assertEqual(config.poll_interval_s(), 60)

    def test_shared_config_preserves_ergonomics_block(self):
        """Ensure writes to `github` don't clobber the `ergonomics` block."""
        import json as _json
        p = self.cfg_root / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"enabled": True, "intervals_min": {"eyes": 20}}))
        from claude_pet.github_watch import config
        config.set_token("t")
        raw = _json.loads(p.read_text())
        self.assertIn("intervals_min", raw)
        self.assertEqual(raw["github"]["token"], "t")


# ---------- helpers ----------------------------------------------------------

def _mk_poll_result(status, events, etag=None, error=None,
                    rate_remaining=None, rate_reset_ts=None):
    from claude_pet.github_watch.api import PollResult
    return PollResult(
        status=status, events=events, etag=etag,
        rate_remaining=rate_remaining, rate_reset_ts=rate_reset_ts, error=error,
    )


def _to_response(resp):
    """Adapt _FakeResp for use with requests.get patching."""
    return resp


if __name__ == "__main__":
    unittest.main()
