"""Test the `claude-pet doctor` self-heal command."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
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


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-pet-doctor-"))
        # Redirect the settings path.
        self.settings = self.tmp / "settings.json"
        p = mock.patch("claude_pet.cli._settings_path", return_value=self.settings)
        p.start()
        self.addCleanup(p.stop)

    def test_doctor_detects_broken_hook_path(self):
        """Given hooks that point to a nonexistent Python, doctor must flag it
        and re-wire to the current interpreter."""
        # Seed a broken settings file with a fake hook path.
        self.settings.write_text(json.dumps({
            "hooks": {
                "SessionStart": [{"hooks": [{
                    "type": "command",
                    "command": '"/does/not/exist/python3" -m claude_pet hook SessionStart',
                    "async": True, "timeout": 2,
                }]}],
            }
        }))
        from claude_pet import cli
        argv = ["claude-pet", "doctor"]
        with mock.patch.object(sys, "argv", argv):
            out = _capture(cli.main)
        # Broken path was surfaced.
        self.assertIn("/does/not/exist/python3", out)
        self.assertIn("✗", out)
        # Doctor re-ran install-hooks — the settings should now reference
        # the current live interpreter.
        data = json.loads(self.settings.read_text())
        commands = []
        for entries in data["hooks"].values():
            for e in entries:
                for h in e.get("hooks", []):
                    commands.append(h.get("command", ""))
        self.assertTrue(
            any(sys.executable in c for c in commands),
            "doctor should have re-wired hooks to sys.executable",
        )

    def test_doctor_reports_no_settings_file(self):
        """Fresh install with no settings.json — doctor exits with instruction."""
        from claude_pet import cli
        # Make sure settings file doesn't exist.
        if self.settings.exists():
            self.settings.unlink()
        argv = ["claude-pet", "doctor"]
        with mock.patch.object(sys, "argv", argv):
            out = _capture(cli.main)
        self.assertIn("not found", out)
        self.assertIn("install-hooks", out)

    def test_doctor_gives_all_clear_on_healthy_install(self):
        """When every hook path points at the current Python, doctor passes."""
        py = sys.executable
        self.settings.write_text(json.dumps({
            "hooks": {
                "Stop": [{"hooks": [{
                    "type": "command",
                    "command": f'"{py}" -m claude_pet hook Stop',
                    "async": True, "timeout": 2,
                }]}],
            }
        }))
        from claude_pet import cli
        argv = ["claude-pet", "doctor"]
        with mock.patch.object(sys, "argv", argv):
            out = _capture(cli.main)
        self.assertIn("all clear", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
