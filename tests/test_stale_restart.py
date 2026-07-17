"""Test the stale-pet auto-replace logic in `claude-pet start` (v0.3.3).

A pet from an older install holding :5050 must be replaced, not tolerated,
so upgrades take effect without the user hunting down stale processes."""

from __future__ import annotations

import io
import sys
import unittest
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


class StaleRestartTests(unittest.TestCase):
    def test_same_version_short_circuits(self):
        from claude_pet import cli, __version__
        with mock.patch.object(cli, "_is_running", return_value=True), \
             mock.patch.object(cli, "_running_version", return_value=__version__), \
             mock.patch.object(cli, "_ask_running_pet_to_quit") as quit_mock, \
             mock.patch.object(cli.subprocess, "Popen") as popen_mock:
            args = mock.Mock(show_in_dock=False)
            out = _capture(lambda: cli.cmd_start(args))
        self.assertIn("already running", out)
        quit_mock.assert_not_called()
        popen_mock.assert_not_called()

    def test_older_version_triggers_replacement(self):
        from claude_pet import cli
        # First _is_running() → True (stale pet holds port);
        # after the quit request → False (port freed) so spawn proceeds.
        running_states = iter([True, False])
        with mock.patch.object(cli, "_is_running",
                               side_effect=lambda: next(running_states)), \
             mock.patch.object(cli, "_running_version", return_value="0.0.1"), \
             mock.patch.object(cli, "_ask_running_pet_to_quit",
                               return_value=True) as quit_mock, \
             mock.patch.object(cli.time, "sleep"), \
             mock.patch.object(cli.subprocess, "Popen") as popen_mock:
            args = mock.Mock(show_in_dock=False)
            out = _capture(lambda: cli.cmd_start(args))
        self.assertIn("restarting", out)
        quit_mock.assert_called_once()
        popen_mock.assert_called_once()

    def test_pre_033_pet_with_no_version_endpoint_is_replaced(self):
        from claude_pet import cli
        running_states = iter([True, False])
        with mock.patch.object(cli, "_is_running",
                               side_effect=lambda: next(running_states)), \
             mock.patch.object(cli, "_running_version", return_value=None), \
             mock.patch.object(cli, "_ask_running_pet_to_quit",
                               return_value=False), \
             mock.patch.object(cli, "cmd_stop") as stop_mock, \
             mock.patch.object(cli.time, "sleep"), \
             mock.patch.object(cli.subprocess, "Popen") as popen_mock:
            args = mock.Mock(show_in_dock=False)
            out = _capture(lambda: cli.cmd_start(args))
        self.assertIn("unknown", out)
        popen_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
