"""Verify CSS custom-property inlining for the exercise SVGs — otherwise
Qt's SVG renderer ignores every var(--…) reference and accent elements
(monitor icons, water glass, arrow guides, dashed rings, countdown labels)
render as no-fill and disappear."""

from __future__ import annotations

import unittest


class SvgInlineTests(unittest.TestCase):
    def test_extract_pulls_root_style_vars(self):
        from claude_pet.ergonomics.svg_inline import _extract_vars
        svg = '<svg style="--acc-cyan:#53D8E8;--acc-blue:#5B8DEF">…</svg>'
        vars_map = _extract_vars(svg)
        self.assertEqual(vars_map["acc-cyan"], "#53D8E8")
        self.assertEqual(vars_map["acc-blue"], "#5B8DEF")

    def test_inline_substitutes_var_references(self):
        from claude_pet.ergonomics.svg_inline import inline_vars
        svg = (
            '<svg style="--acc-cyan:#53D8E8">'
            '<circle style="fill:var(--acc-cyan)"/>'
            '<rect style="stroke:var(--acc-cyan);fill:none"/>'
            '</svg>'
        )
        out = inline_vars(svg)
        self.assertNotIn("var(--", out)
        self.assertIn("fill:#53D8E8", out)
        self.assertIn("stroke:#53D8E8", out)

    def test_unresolved_var_left_intact(self):
        """Unknown var should stay literal — Qt then falls back to its
        default (invisible), which matches pre-inline behaviour."""
        from claude_pet.ergonomics.svg_inline import inline_vars
        svg = '<svg style="--x:red"><circle style="fill:var(--y)"/></svg>'
        out = inline_vars(svg)
        self.assertIn("var(--y)", out)

    def test_every_catalog_svg_has_zero_unresolved_vars_after_inline(self):
        """The catalog SVGs must resolve completely — this is the guarantee
        that decorations render on user machines."""
        from claude_pet.ergonomics import exercises
        from claude_pet.ergonomics.svg_inline import load_inlined
        for e in exercises.CATALOG:
            inlined = load_inlined(e.svg_path()).decode("utf-8")
            self.assertNotIn(
                "var(--", inlined,
                f"{e.slug} has unresolved CSS vars — decorations will be invisible",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
