"""Exercise catalog — data only, no logic.

Each entry ships a self-animating SMIL SVG (rendered as-is by QSvgWidget)
plus display metadata. To add an exercise: drop the SVG in svgs/ and append
a row here. No code changes anywhere else.

Categories map 1:1 to scheduler rotation slots — keep them stable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


Category = Literal["eyes", "neck", "shoulders", "wrists", "posture", "hydration"]


@dataclass(frozen=True)
class Exercise:
    slug: str
    name: str                # display title (≤24 chars)
    category: Category
    duration_s: int          # target time on the overlay before auto-advance
    reps: int                # 0 = timed hold (no rep count shown)
    svg_file: str            # basename inside svgs/
    instruction: str         # ≤80 chars, imperative voice

    def svg_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "svgs", self.svg_file)


# v0.4.0 — 5 exercises spanning 5 categories (adopted from user's SVG library).
# Grow this later; every entry must have its SVG present.
CATALOG: tuple[Exercise, ...] = (
    Exercise(
        slug="eye-break",
        name="Eye break (20-20-20)",
        category="eyes",
        duration_s=20,
        reps=0,
        svg_file="pet-eye-break.svg",
        instruction="Look at anything ~6 m away, keep blinking. Switch sides.",
    ),
    Exercise(
        slug="chin-tuck",
        name="Chin tuck",
        category="neck",
        duration_s=60,
        reps=10,
        svg_file="pet-chin-tuck.svg",
        instruction="Glide head straight back — no tilting. Hold 5 s each.",
    ),
    Exercise(
        slug="wrist-circles",
        name="Wrist circles",
        category="wrists",
        duration_s=24,
        reps=8,
        svg_file="pet-wrist-circles.svg",
        instruction="Both fists out, circles in opposite directions.",
    ),
    Exercise(
        slug="reach-high",
        name="Reach high",
        category="posture",
        duration_s=20,
        reps=2,
        svg_file="pet-reach-high.svg",
        instruction="Arms overhead, stretch tall — hold — repeat.",
    ),
    Exercise(
        slug="water-break",
        name="Water break",
        category="hydration",
        duration_s=15,
        reps=0,
        svg_file="pet-water-break.svg",
        instruction="Small sip. Aim for regular through the day, not gulps.",
    ),
)


CATALOG_BY_SLUG: dict[str, Exercise] = {e.slug: e for e in CATALOG}
CATALOG_BY_CATEGORY: dict[str, tuple[Exercise, ...]] = {}
for _e in CATALOG:
    CATALOG_BY_CATEGORY.setdefault(_e.category, ())
    CATALOG_BY_CATEGORY[_e.category] = CATALOG_BY_CATEGORY[_e.category] + (_e,)


# Ordered rotation — the scheduler cycles through these categories in order,
# so no muscle group is ever skipped. Eyes come first because they're the
# most-often prompted cue (every 20 min).
ROTATION: tuple[Category, ...] = (
    "eyes", "neck", "wrists", "posture", "hydration",
)


def get(slug: str) -> Exercise | None:
    return CATALOG_BY_SLUG.get(slug)


def for_category(cat: Category) -> Exercise | None:
    """Return the first exercise for a category (v0.4.0 has one each)."""
    items = CATALOG_BY_CATEGORY.get(cat)
    return items[0] if items else None


def all_slugs() -> tuple[str, ...]:
    return tuple(e.slug for e in CATALOG)
