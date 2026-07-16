"""Skill promotion — turn reinforced graph nodes into Claude Code skills.

When a node's reinforcement count reaches 2, it graduates to a skill: a
SKILL.md file with valid frontmatter written under
`~/.claude/claude-pet/skills/<slug>/`. Skills persist across projects and
are picked up by Claude Code's skill discovery.

Levels compound: 2×=hatchling (lvl 1), 4×=apprentice (lvl 2), 8×=senior (lvl 3),
16×=ponytail (lvl 4+). This is `floor(log2(reinforcements)) + 1`.
"""

from __future__ import annotations

import math
import re
import os
from pathlib import Path
from typing import Iterable

from . import memory


PROMOTION_THRESHOLD = 2  # reinforcements at which we first create the SKILL.md


def _skills_dir() -> Path:
    """Write skills directly into Claude Code's user-scoped skill discovery
    path — `~/.claude/skills/` — so the generated SKILL.md files are picked
    up automatically. We namespace every slug with `claude-pet-` so our
    files never collide with hand-authored skills.

    Previously we wrote to `~/.claude/claude-pet/skills/` which Claude Code
    does NOT scan — the files existed but were invisible to the model."""
    root = Path.home() / ".claude" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


_SKILL_PREFIX = "claude-pet-"


def _slugify(text: str, max_len: int = 60) -> str:
    """Filesystem-safe kebab-case slug. Deterministic for the same input."""
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        s = "unnamed"
    return s[:max_len]


def _title_case(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("-", " ").split())


def _tier_for_level(level: int) -> str:
    return {1: "hatchling", 2: "apprentice", 3: "senior"}.get(level, "ponytail")


def _skill_body(title: str, description: str, tier: str, level: int,
                reinforcements: int, source_project: str) -> str:
    """A valid Claude Code SKILL.md — YAML frontmatter + markdown body."""
    # Truncate description to fit the ~1024-char skill listing budget.
    desc = description.strip().replace("\n", " ")
    if len(desc) > 400:
        desc = desc[:397] + "…"
    return (
        "---\n"
        f"name: {title}\n"
        f"description: {desc}\n"
        "metadata:\n"
        f"  tier: {tier}\n"
        f"  level: {level}\n"
        f"  reinforcements: {reinforcements}\n"
        f"  source_project: {source_project}\n"
        "  source: claude-pet\n"
        "---\n\n"
        f"# {title}\n\n"
        f"_Tier: **{tier}**, level **{level}**, "
        f"reinforced **{reinforcements}×** in `{source_project}`._\n\n"
        "## When this pattern applies\n\n"
        f"{description}\n\n"
        "## Why the pet promoted this\n\n"
        "Claude Pet noticed you followed this pattern in more than one session "
        "in the same project. Repetition = signal. This skill will surface "
        "automatically when you're in a matching context.\n"
    )


def maybe_promote_node(node: dict) -> dict | None:
    """If a node crosses the promotion threshold, write/update its SKILL.md
    on disk and upsert the `skills` row. Returns the skill dict or None."""
    reinforcements = node.get("reinforcements") or 1
    if reinforcements < PROMOTION_THRESHOLD:
        return None

    project_path = node["project_path"]
    project_slug = _slugify(os.path.basename(project_path.rstrip(os.sep)) or "project")
    key = node["key"]
    slug = f"{_SKILL_PREFIX}{project_slug}-{_slugify(key)}"

    title = _title_case(slug)
    description = node["value"]

    disk_dir = _skills_dir() / slug
    disk_dir.mkdir(parents=True, exist_ok=True)
    skill_md = disk_dir / "SKILL.md"

    # Write DB record first (to get level/tier), then write the disk file.
    # The node's reinforcement count is authoritative — the skill mirrors it.
    result = memory.upsert_skill(
        slug=slug,
        title=title,
        description=description,
        project_path=project_path,
        source_node_ids=[node["id"]],
        disk_path=str(skill_md),
        reinforcements=reinforcements,
    )
    body = _skill_body(
        title=title,
        description=description,
        tier=result["tier"],
        level=result["level"],
        reinforcements=result["reinforcements"],
        source_project=project_path,
    )
    skill_md.write_text(body)
    return result


def scan_and_promote(project_path: str) -> list[dict]:
    """Walk every node in the project and promote whichever have earned it.
    Called from the Stop hook after `distill_session` runs."""
    promoted: list[dict] = []
    with memory.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE project_path = ? AND reinforcements >= ?",
            (project_path, PROMOTION_THRESHOLD),
        ).fetchall()
    for r in rows:
        result = maybe_promote_node(dict(r))
        if result:
            promoted.append(result)
    return promoted
