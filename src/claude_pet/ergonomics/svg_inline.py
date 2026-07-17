"""Inline CSS custom-property values into an SVG.

Qt's QSvgRenderer (through v6) doesn't process `--name: color` custom
properties or `var(--name)` fallbacks. The user's exercise SVGs put every
accent color into custom properties on the root <svg style="…"> block, so
every accent element (monitor icons, water, "20 ft" labels, arrow guides,
countdown rings) rendered with no fill = invisible.

This module reads the SVG once, extracts the var → color map from the root
style attribute, and substitutes every `var(--name)` occurrence with the
resolved color. Result: identical visual output to a browser, at load time.
Cached so a break overlay reloading the same exercise pays the parse cost
only once."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# Matches an SVG root style attribute — captures its content.
_ROOT_STYLE_RE = re.compile(
    r'<svg\b[^>]*?\bstyle="([^"]*)"',
    re.DOTALL,
)

# --name: value pairs inside a style attribute.
_VAR_DEF_RE = re.compile(r"--([A-Za-z0-9_-]+)\s*:\s*([^;]+)")

# `var(--name)` (with optional fallback we ignore for simplicity).
_VAR_USE_RE = re.compile(r"var\(--([A-Za-z0-9_-]+)(?:\s*,\s*[^)]+)?\)")


def _extract_vars(svg_text: str) -> dict[str, str]:
    """Pull the --name: value map out of the root <svg style="…">."""
    m = _ROOT_STYLE_RE.search(svg_text)
    if not m:
        return {}
    style = m.group(1)
    return {name: value.strip() for name, value in _VAR_DEF_RE.findall(style)}


def inline_vars(svg_text: str) -> str:
    """Replace every var(--name) with its resolved color from the root style.
    Unresolved refs are left as-is (Qt then renders them as no-fill, which
    is the pre-fix behaviour — no regression for unknown vars)."""
    vars_map = _extract_vars(svg_text)
    if not vars_map:
        return svg_text

    def sub(match: re.Match) -> str:
        name = match.group(1)
        return vars_map.get(name, match.group(0))

    return _VAR_USE_RE.sub(sub, svg_text)


@lru_cache(maxsize=32)
def load_inlined(path: str) -> bytes:
    """Read an SVG file and return its custom-property-resolved bytes.
    Cached — subsequent calls hit an in-memory cache."""
    text = Path(path).read_text(encoding="utf-8")
    inlined = inline_vars(text)
    return inlined.encode("utf-8")
