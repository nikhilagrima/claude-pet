"""Tests for the pet-wide sound mute feature (v0.5.7)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class PetConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg_dir = Path(tempfile.mkdtemp(prefix="claude-pet-mute-"))
        p = mock.patch("claude_pet.pet_config._config_path",
                       return_value=self.cfg_dir / "config.json")
        p.start(); self.addCleanup(p.stop)

    def test_default_is_unmuted(self):
        from claude_pet import pet_config
        self.assertFalse(pet_config.is_muted())

    def test_set_and_read_roundtrip(self):
        from claude_pet import pet_config
        pet_config.set_muted(True)
        self.assertTrue(pet_config.is_muted())
        pet_config.set_muted(False)
        self.assertFalse(pet_config.is_muted())

    def test_toggle_returns_new_state(self):
        from claude_pet import pet_config
        self.assertTrue(pet_config.toggle_muted())     # was False → True
        self.assertFalse(pet_config.toggle_muted())    # → False
        self.assertTrue(pet_config.toggle_muted())     # → True

    def test_config_survives_reload(self):
        """A restart must see the last-persisted state."""
        from claude_pet import pet_config
        pet_config.set_muted(True)
        # Simulate a restart by re-reading everything.
        self.assertTrue(pet_config.is_muted())
        # Also verify the on-disk shape has the top-level "pet" key so it
        # coexists with ergonomics + github blocks without conflict.
        import json as _json
        raw = _json.loads((self.cfg_dir / "config.json").read_text())
        self.assertIn("pet", raw)
        self.assertTrue(raw["pet"]["muted"])

    def test_coexists_with_ergonomics_block(self):
        """Writing pet.muted must not clobber a pre-existing ergonomics block."""
        import json as _json
        (self.cfg_dir / "config.json").write_text(_json.dumps({
            "enabled": True,
            "intervals_min": {"eyes": 25},
        }))
        from claude_pet import pet_config
        pet_config.set_muted(True)
        raw = _json.loads((self.cfg_dir / "config.json").read_text())
        # ergonomics block preserved
        self.assertIn("intervals_min", raw)
        self.assertEqual(raw["intervals_min"]["eyes"], 25)
        # pet block added
        self.assertTrue(raw["pet"]["muted"])


class SoundPlayerMuteTests(unittest.TestCase):
    """SoundPlayer.play() must no-op when muted, without touching disk audio."""

    def setUp(self):
        self.cfg_dir = Path(tempfile.mkdtemp(prefix="claude-pet-mute-sp-"))
        p = mock.patch("claude_pet.pet_config._config_path",
                       return_value=self.cfg_dir / "config.json")
        p.start(); self.addCleanup(p.stop)

    def test_muted_play_does_not_spawn_audio(self):
        from claude_pet import pet_config
        from claude_pet.app import SoundPlayer
        pet_config.set_muted(True)
        sp = SoundPlayer()
        with mock.patch("claude_pet.app._play_audio") as spawned:
            sp.play("success")
        self.assertEqual(spawned.call_count, 0,
                         "muted play() must NOT spawn an audio process")

    def test_unmuted_play_spawns_audio(self):
        from claude_pet import pet_config
        from claude_pet.app import SoundPlayer
        pet_config.set_muted(False)
        sp = SoundPlayer()
        # Only assert if there's actually a resolvable sound file for this
        # platform — sounds dict might be empty on minimal CI containers.
        if not sp.sounds.get("success"):
            self.skipTest("no bundled/system 'success' sound on this host")
        with mock.patch("claude_pet.app._play_audio",
                        return_value=None) as spawned:
            sp.play("success")
        self.assertGreaterEqual(spawned.call_count, 1)


if __name__ == "__main__":
    unittest.main()
