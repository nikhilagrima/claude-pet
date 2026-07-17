"""Scheduler — decides when to prompt a break and which exercise to show.

Not a background thread. `check_due()` is called from the pet's existing
Qt animation timer (10 Hz-ish is plenty; break thresholds are minutes).
That way we add zero new daemons or event loops.

Deferral logic: a prompt CAN fire if activity is happening, but MUST NOT
fire if the user is mid-type. We approximate 'user is typing a prompt' by
'the current pet status is idle AND last activity was <5 s ago' — the pet's
status flips to 'thinking' on UserPromptSubmit, so the typing window is
exactly the pre-submit gap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from . import exercises, tracker


# Thresholds in ACTIVE seconds (not wall clock). Adopted from AOA/HSE/OSHA
# — see docs/RESEARCH-ERGONOMICS.md.
DEFAULT_THRESHOLDS = {
    "eyes":      20 * 60,
    "neck":      30 * 60,
    "wrists":    45 * 60,
    "posture":   60 * 60,
    "hydration": 60 * 60,
}

# Categories to consider prompting for, in preferred order when several are due.
CHECK_ORDER = ("eyes", "neck", "wrists", "posture", "hydration")

# Never defer for more than this — if the user has been typing continuously
# for 5 min, we prompt anyway rather than skip a needed break entirely.
MAX_DEFER_S = 5 * 60


@dataclass
class BreakPrompt:
    category: str
    exercise_slug: str
    overdue_by_s: float


def check_due(
    pet_status: str,
    last_activity_at: float,
    now: float | None = None,
    thresholds: dict | None = None,
    pending_since: float | None = None,
) -> BreakPrompt | None:
    """Return a BreakPrompt if one is due right now, else None.

    Args:
      pet_status:     current mascot state ('idle', 'thinking', 'writing', …).
      last_activity_at: time.time() of the last real activity event.
      now:            override for tests; defaults to time.time().
      thresholds:     override defaults per-category.
      pending_since:  if we already decided to prompt but deferred, this is
                      when — used to enforce MAX_DEFER_S.
    """
    now = now if now is not None else time.time()
    thresholds = thresholds or DEFAULT_THRESHOLDS

    # Global pauses.
    if pet_status == "sleeping":
        return None                    # 3-min-idle sleep state pauses everything
    if pet_status == "error":
        return None                    # don't interrupt a failing run

    # If we've been deferring past the cap, force the prompt through regardless
    # of what the user's doing — a break they never take is worse than a
    # slightly-disruptive one.
    force_through = (
        pending_since is not None and (now - pending_since) >= MAX_DEFER_S
    )

    if not force_through:
        # Don't interrupt typing. Approximation: pet is 'idle' AND user just
        # tapped a key <5s ago — real typing keeps this true; genuine idle
        # doesn't (last_activity_at drifts old, so this predicate goes false).
        if pet_status == "idle" and (now - last_activity_at) < 5:
            return None

    # Pick the category most overdue.
    best: BreakPrompt | None = None
    for cat in CHECK_ORDER:
        threshold = thresholds.get(cat, 0)
        if threshold <= 0:
            continue                    # category disabled in config
        elapsed = tracker.active_seconds_since_last(cat)
        overdue = elapsed - threshold
        if overdue < 0:
            continue                    # not due yet
        exercise = exercises.for_category(cat)
        if exercise is None:
            continue                    # catalog missing — skip silently
        if best is None or overdue > best.overdue_by_s:
            best = BreakPrompt(
                category=cat,
                exercise_slug=exercise.slug,
                overdue_by_s=overdue,
            )
    return best


def snooze_until(seconds_from_now: float) -> float:
    """Return an epoch timestamp; the caller stores it as the "don't prompt
    before this time" gate. Snoozing sets a wall-clock cap in addition to
    activity-based thresholds — user meant 'leave me alone for X minutes'."""
    return time.time() + max(seconds_from_now, 0.0)
