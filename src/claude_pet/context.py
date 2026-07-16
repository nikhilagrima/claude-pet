"""Context assembler — builds the ≤N-token block injected into Claude Code
on SessionStart via the hook's `hookSpecificOutput.additionalContext`.

Deterministic, no LLM calls, no network. Budget enforced by construction:
each section has a max character cap; if a section overflows, it truncates
in place; the never-cut safety block is appended last.
"""

from __future__ import annotations

import os
from typing import Iterable

from . import memory


# ---------------------------------------------------------------------------
# Budget config — keep everything in one place for benchmarking.
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4        # rough proxy; deterministic; no tiktoken dep.
DEFAULT_TOKENS = 800

# Per-section character caps (sum = 3200 = 800 tokens at 4:1 ratio).
CAPS = {
    "header":     300,
    "recap":      400,
    "nodes":      1800,
    "skills":     400,
    "known":      200,
    "safety":     100,   # never trimmed
}

# The safety ruleset is adopted from ponytail's ladder + carve-outs.
# Kept tight (≤ 100 chars) so it always fits inside the budget.
SAFETY_RULES = (
    "Rules: reuse memory before re-reading files; never skip validation, "
    "security, or accessibility to save tokens."
)


def _trim(text: str, cap: int) -> str:
    """Cut to `cap` characters. Snip on the last space to avoid mid-word cuts."""
    if len(text) <= cap:
        return text
    cut = text[:cap].rsplit(" ", 1)[0]
    return cut + "…"


def _project_header(summary: dict, cap: int) -> str:
    if not summary.get("known"):
        return ""
    t = summary["totals"]
    tools = ", ".join(x["tool_name"] for x in summary["top_tools"][:3]) or "none"
    header = (
        f"# {summary['name']} — pet memory\n"
        f"Sessions here: {t['sessions']}  "
        f"tool calls: {t['tool_calls']}  "
        f"successes: {t['successes']}  errors: {t['errors']}\n"
        f"First seen: {summary['first_seen']}\n"
        f"Most-used tools: {tools}"
    )
    return _trim(header, cap)


def _recap(summary: dict, cap: int) -> str:
    sessions = summary.get("recent_sessions") or []
    notes = summary.get("notes") or []
    if not sessions and not notes:
        return ""
    lines = ["## Last session"]
    if sessions:
        s = sessions[0]
        ended = s.get("ended_at") or "still open"
        lines.append(
            f"- {s['started_at']} → {ended}  "
            f"({s['tool_calls']} calls, {s['successes']}✓, {s['errors']}✗)"
        )
    if notes:
        lines.append("## Latest note" + ("s" if len(notes) > 1 else ""))
        for n in notes[:2]:
            lines.append(f"- {n['note']}")
    return _trim("\n".join(lines), cap)


def _nodes_section(project_path: str, cap: int) -> tuple[str, list[str]]:
    """Return (text_block, list_of_known_file_paths)."""
    ranked = memory.top_nodes(project_path, limit=30)
    if not ranked:
        return "", []
    lines = ["## Prior context (weighted)"]
    known_files: set[str] = set()
    running = len(lines[0]) + 1
    for r in ranked:
        # Skip anything already in the block via file dedup; still record file.
        if r.get("file_path"):
            known_files.add(r["file_path"])
        prefix = {
            "decision":   "★",
            "convention": "•",
            "fix":        "✎",
            "gotcha":     "⚠",
        }.get(r["kind"], "·")
        line = f"{prefix} [{r['kind']}] {r['value']}"
        if len(line) > 240:
            line = line[:238] + "…"
        if running + len(line) + 1 > cap:
            break
        lines.append(line)
        running += len(line) + 1
    return "\n".join(lines), sorted(known_files)


def _skills_section(cap: int) -> str:
    skills = memory.list_skills()
    if not skills:
        return ""
    tier_icon = {"hatchling": "🥚", "apprentice": "🐣",
                 "senior": "🦉", "ponytail": "🦄"}
    lines = ["## Learned skills"]
    running = len(lines[0]) + 1
    for s in skills[:20]:
        icon = tier_icon.get(s["tier"], "·")
        line = f"{icon} {s['title']} (lvl {s['level']}, {s['tier']}, {s['reinforcements']}×)"
        if running + len(line) + 1 > cap:
            break
        lines.append(line)
        running += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _known_files(paths: list[str], cap: int) -> str:
    if not paths:
        return ""
    header = "## Already indexed (skip re-reading unless changed)"
    joined = ", ".join(paths)
    if len(header) + 1 + len(joined) > cap:
        joined = _trim(joined, cap - len(header) - 2)
    return f"{header}\n{joined}"


def _safety_block() -> str:
    return f"## Safety rules (never trim)\n{SAFETY_RULES}"


def build_context(
    project_path: str | None = None,
    token_budget: int = DEFAULT_TOKENS,
) -> str:
    """Assemble a single string ≤ `token_budget * CHARS_PER_TOKEN` chars.

    The safety block is ALWAYS included and is added last — the rest gets
    trimmed if we run over. Deterministic for identical DB state + args."""
    project_path = project_path or memory.current_project()
    char_budget = token_budget * CHARS_PER_TOKEN

    summary = memory.project_summary(project_path)

    parts: list[str] = []
    header = _project_header(summary, CAPS["header"])
    if header:
        parts.append(header)

    recap = _recap(summary, CAPS["recap"])
    if recap:
        parts.append(recap)

    nodes_block, known_paths = _nodes_section(project_path, CAPS["nodes"])
    if nodes_block:
        parts.append(nodes_block)

    skills_block = _skills_section(CAPS["skills"])
    if skills_block:
        parts.append(skills_block)

    known_block = _known_files(known_paths, CAPS["known"])
    if known_block:
        parts.append(known_block)

    # Reserve room for the safety block. If body overflows, trim from the end.
    safety = _safety_block()
    body = "\n\n".join(parts)
    room = char_budget - len(safety) - 2   # for the joining newlines
    if len(body) > room:
        body = _trim(body, room)

    return f"{body}\n\n{safety}" if body else safety


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
