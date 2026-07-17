"""Phase 5 tests — panel importability, tier overlay in SVG, click handler.

We don't spin up a real Qt event loop (that would need a display server on
CI Linux); instead we test the SVG output shape and the panel-module surface.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TierOverlayTests(unittest.TestCase):
    def test_svg_omits_tier_badge_by_default(self):
        from claude_pet.bot_svg import make_svg
        svg = make_svg("idle")
        # No tier means no orange/etc. badge stroke — check by absence of
        # the tier-badge fills that only appear with a tier.
        for badge_color in ("#F97316", "#60A5FA", "#22D3EE", "#3FA3FF"):
            # These colors appear in other places too — the specific test:
            # the badge is a small circle+pips block. Look for the badge stroke.
            pass  # can't uniquely detect absence
        # Instead: assert the count of <circle> elements is lower without tier
        base = svg.count("<circle")
        with_tier = make_svg("idle", tier="master").count("<circle")
        self.assertGreater(with_tier, base,
                           "adding a tier must add ≥1 extra <circle> for the badge")

    def test_svg_supports_all_tiers(self):
        from claude_pet.bot_svg import make_svg
        for tier in ("hatchling", "apprentice", "senior", "master"):
            svg = make_svg("idle", tier=tier)
            self.assertIn("<svg", svg)
            self.assertIn("</svg>", svg)

    def test_all_eleven_emotions_still_render(self):
        """Regression: don't break the existing pet on the memory upgrade."""
        from claude_pet.bot_svg import make_svg, EMOTIONS
        for emotion in EMOTIONS:
            svg = make_svg(emotion)
            self.assertTrue(svg.startswith("<svg"))
            self.assertTrue(svg.endswith("</svg>"))
        self.assertEqual(len(EMOTIONS), 11)

    def test_tier_pip_count_matches_level(self):
        """Tier maps 1→hatchling(1 pip) ... 4→master(4 pips)."""
        from claude_pet.bot_svg import make_svg
        for tier, expected_pips in [
            ("hatchling", 1),
            ("apprentice", 2),
            ("senior", 3),
            ("master", 4),
        ]:
            svg = make_svg("idle", tier=tier)
            # Count the small pip circles (r=1.2). Regex is safer than parsing SVG.
            pips = re.findall(r'r="1\.2"', svg)
            self.assertEqual(
                len(pips), expected_pips,
                f"{tier} should render {expected_pips} pip(s), got {len(pips)}",
            )


class PanelImportTests(unittest.TestCase):
    """The panel module must be importable without a running Qt event loop.
    (Instantiating widgets requires a QApplication; we only check the API.)"""

    def test_panel_module_imports_cleanly(self):
        # PySide6's widget classes get imported at module load; but their
        # constructors need QApplication. Just importing must succeed —
        # unless the host lacks GUI libraries entirely (headless server
        # without libEGL), in which case skipping is correct behavior.
        try:
            from claude_pet import panel
        except ImportError as e:
            self.skipTest(f"GUI libraries unavailable on this host: {e}")
        self.assertTrue(hasattr(panel, "MemoryPanel"))
        self.assertTrue(hasattr(panel, "ProjectsTab"))
        self.assertTrue(hasattr(panel, "GraphTab"))
        self.assertTrue(hasattr(panel, "SkillsTab"))
        self.assertTrue(hasattr(panel, "StatsTab"))
        self.assertTrue(hasattr(panel, "ErgonomicsTab"))
        self.assertTrue(hasattr(panel, "GithubTab"))
        # Tier icon/color tables must cover all 4 tiers.
        for tier in ("hatchling", "apprentice", "senior", "master"):
            self.assertIn(tier, panel.TIER_COLOR)
            self.assertIn(tier, panel.TIER_ICON)


if __name__ == "__main__":
    unittest.main(verbosity=2)
