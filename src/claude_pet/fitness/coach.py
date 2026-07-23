"""Coach — daily nudge text + Claude Code bridge for weekly adjustments.

Purely local text generation for daily nudges; delegates the WEEKLY plan
adjustment to Claude Code via the pet's existing SessionStart context
injection (see hook.py + context.py). Never calls an API directly.

Bridge protocol:
  1. On Sunday when no note for the current ISO week exists,
     `weekly_adjustment_pending()` returns True.
  2. The pet's SessionStart hook adds a compact instruction block asking
     Claude Code to write a <60-word coaching note to
     ~/.claude/claude-pet/fitness_note.txt.
  3. Once written, `latest_note()` reads it; the pet shows it in a bubble
     and marks it shown so the same note isn't re-shown across restarts.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import config as fcfg
from . import plan as fplan
from . import tracker


# --- LOCAL DAILY NUDGE ------------------------------------------------------

def daily_nudge(now: Optional[datetime] = None) -> str:
    """Return the pet's morning bubble text for today. Local only.

    Contains: today's focus + body parts + lifts + cardio, and the computed
    kcal/protein/step targets from the user's profile. Deliberately terse —
    the pet bubble has limited real estate.
    """
    now = now or datetime.now()
    day = fplan.day_plan_for(now.weekday())
    prof = fcfg.profile()
    targets = fplan.daily_targets(
        weight_kg=float(prof.get("weight_kg", 80)),
        height_cm=float(prof.get("height_cm", 175)),
        age=int(prof.get("age", 30)),
        male=bool(prof.get("male", True)),
        activity_factor=float(prof.get("activity_factor", 1.375)),
    )
    lines = [day.label]
    if day.body_parts:
        lines.append("body: " + ", ".join(day.body_parts))
    if day.lifts:
        lines.append("lifts: " + " · ".join(day.lifts))
    if day.cardio:
        lines.append(day.cardio)
    lines.append("")
    lines.append(
        f"target: {targets.target_kcal} kcal · "
        f"{targets.protein_g} g protein · "
        f"{targets.steps:,} steps"
    )
    return "\n".join(lines)


# --- CLAUDE-CODE BRIDGE -----------------------------------------------------

def _iso_week_id(d: Optional[date] = None) -> str:
    """e.g. '2026-W30'. Compared as a string for equality only."""
    d = d or date.today()
    year, week, _ = d.isocalendar()
    return f"{year:04d}-W{week:02d}"


def weekly_adjustment_pending() -> bool:
    """True iff (a) agentic coach enabled AND (b) today is Sunday (weekday 6)
    AND (c) no note has been generated for the current ISO week."""
    if not fcfg.agentic_coach_enabled():
        return False
    if date.today().weekday() != 6:
        return False
    cfg = fcfg.load()
    generated_week = str(cfg.get("_week_note_generated") or "")
    return generated_week != _iso_week_id()


def mark_week_note_generated() -> None:
    """Call once the SessionStart context-injection has printed the ask.
    Prevents multiple sessions in the same week from all being asked.
    """
    cfg = fcfg.load()
    cfg["_week_note_generated"] = _iso_week_id()
    fcfg.save(cfg)


def build_weekly_adjustment_context() -> str:
    """The block hook.py adds to Claude Code's SessionStart injection when
    a weekly adjustment is pending. Compact — meant to be a small tail on
    the existing memory context, not a whole prompt.
    """
    prof = fcfg.profile()
    targets = fplan.daily_targets(
        weight_kg=float(prof.get("weight_kg", 80)),
        height_cm=float(prof.get("height_cm", 175)),
        age=int(prof.get("age", 30)),
        male=bool(prof.get("male", True)),
        activity_factor=float(prof.get("activity_factor", 1.375)),
    )
    recent = tracker.recent(days=14)
    lines = ["", "## Weekly fitness adjustment (please write ≤60 words)"]
    lines.append(
        f"Profile: {prof.get('weight_kg')}kg → target "
        f"{prof.get('target_weight_kg')}kg  ·  "
        f"targets: {targets.target_kcal} kcal / "
        f"{targets.protein_g} g protein / {targets.steps} steps"
    )
    # Baseline plan summary — one-liners so the injection stays small
    lines.append("Plan: Mon PUSH · Tue Cardio+Core · Wed PULL · "
                 "Thu HIIT · Fri LEGS · Sat Recovery · Sun Rest")
    lines.append(f"Last 14 days: {len(recent['weights'])} weigh-ins, "
                 f"{len(recent['workouts'])} workouts, "
                 f"{len(recent['meals'])} meal check-ins")
    if recent["weights"]:
        weights = recent["weights"]
        first, last = weights[-1], weights[0]     # oldest → newest by index
        lines.append(f"Weight trend: {first['day']} {first['weight_kg']}kg "
                     f"→ {last['day']} {last['weight_kg']}kg")
    completed = sum(1 for w in recent["workouts"] if w["completed"])
    lines.append(f"Workouts completed: {completed}")
    on_plan = sum(1 for m in recent["meals"] if m["on_plan"])
    lines.append(f"Meals on plan: {on_plan} / {len(recent['meals'])}")
    lines.append("")
    lines.append(
        "Please write a <60-word plain-text coaching note (what to keep, "
        "what to change this week) to ~/.claude/claude-pet/fitness_note.txt. "
        "Do not use markdown, do not sign it. Just the note."
    )
    return "\n".join(lines)


def latest_note() -> Optional[str]:
    """Read the fitness note if present. Returns None if empty/missing."""
    p = fcfg._fitness_note_path()
    if not p.exists():
        return None
    try:
        txt = p.read_text().strip()
        return txt or None
    except Exception:
        return None


def note_needs_showing() -> bool:
    """True iff the note file exists AND its mtime is newer than the last
    date we recorded showing it."""
    p = fcfg._fitness_note_path()
    if not p.exists():
        return False
    cfg = fcfg.load()
    shown = str(cfg.get("_note_shown_date") or "")
    try:
        mtime_day = date.fromtimestamp(p.stat().st_mtime).isoformat()
    except Exception:
        return False
    return mtime_day > shown


def mark_note_shown() -> None:
    cfg = fcfg.load()
    cfg["_note_shown_date"] = date.today().isoformat()
    fcfg.save(cfg)
