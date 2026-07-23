"""3-stage reminder scheduler.

Called from the pet's Qt tick loop (~3s cadence). Returns the list of
(reminder, stage) pairs that need to fire RIGHT NOW — pre-filtered against
each reminder's `fired_stages` so the caller can just render bubbles and
call `store.mark_stage_fired` for each.

Stage windows:
  - "day_before"  fires when now >= due − 24h  and stage not yet fired
                  AND stage is not "past due" (we don't retro-fire day-before
                  for a reminder already past its due time)
  - "five_min"    fires when now >= due − 5min and stage not yet fired
                  AND now < due + 10min  (no retro-fire deep past due)
  - "on_time"     fires when now >= due       and stage not yet fired
                  (always fires, even late — you want to know you missed it)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from . import store


STAGE_ORDER = ("day_before", "five_min", "on_time")

# How far past the exact moment we still allow the earlier stages to fire.
# Prevents "5-min before" popping up for something that's already 3 hours late.
_FIVE_MIN_GRACE_MIN = 10
_DAY_BEFORE_GRACE_H = 12


@dataclass
class ReminderStage:
    reminder: dict
    stage: str


def _parse_due(due_iso: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(due_iso)
    except Exception:
        return None


def check_due(now: Optional[datetime] = None) -> list[ReminderStage]:
    """Return every (reminder, stage) that should fire right now, pre-
    filtered so `mark_stage_fired` never sees a duplicate."""
    now = now or datetime.now()
    firing: list[ReminderStage] = []
    for r in store.list_active():
        due = _parse_due(r["due_at"])
        if due is None:
            continue
        fired = set(r["fired_stages"])
        # on_time — fires whenever now >= due (even late)
        if "on_time" not in fired and now >= due:
            firing.append(ReminderStage(reminder=r, stage="on_time"))
            continue    # highest-priority stage; skip earlier ones for this r
        # five_min — fires when now is in [due-5m, due+grace]
        five_min_start = due - timedelta(minutes=5)
        five_min_end = due + timedelta(minutes=_FIVE_MIN_GRACE_MIN)
        if "five_min" not in fired and five_min_start <= now < five_min_end:
            firing.append(ReminderStage(reminder=r, stage="five_min"))
            continue
        # day_before — fires when now is in [due-24h, due-12h] roughly
        day_start = due - timedelta(hours=24)
        day_end = due - timedelta(hours=_DAY_BEFORE_GRACE_H)
        if ("day_before" not in fired
                and day_start <= now < day_end):
            firing.append(ReminderStage(reminder=r, stage="day_before"))
    return firing


def label_for_stage(stage: str) -> str:
    return {
        "day_before": "Tomorrow",
        "five_min":   "In 5 minutes",
        "on_time":    "Now",
    }.get(stage, stage)
