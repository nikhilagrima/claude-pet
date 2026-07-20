"""Glue: poll each watched repo, classify events, store new ones.

Callers (the pet's tick loop or `claude-pet github check`) invoke
`poll_all_due()`. Alert delivery is handled elsewhere by reading
`storage.pending_alerts()` and calling `storage.mark_alerted()` per event.
"""

from __future__ import annotations

import time
from typing import Iterable

from . import api, classify, config, storage


# In-memory rate-limit backoff (per process). If GitHub says we're rate-limited
# we won't hit ANY /events endpoint again until this timestamp.
_rate_block_until: float = 0.0

# Per-watch next-poll-at timestamp (avoids hammering repos more often than
# the configured interval even when the tick loop is fast).
_next_poll_at: dict[int, float] = {}

# Per (watch_id, event_type) last-alerted timestamp. Used by the cooldown
# to suppress duplicate alerts within a short window — e.g. rapid WIP
# pushes triggering N PushEvents in a minute should only ding once.
COOLDOWN_SECONDS = 300      # 5 minutes; matches AUDIT top-5 #5
_last_alerted_at: dict[tuple[int, str], float] = {}


def _in_cooldown(watch_id: int, event_type: str) -> bool:
    key = (watch_id, event_type)
    last = _last_alerted_at.get(key, 0.0)
    return (time.time() - last) < COOLDOWN_SECONDS


def _mark_alerted(watch_id: int, event_type: str) -> None:
    _last_alerted_at[(watch_id, event_type)] = time.time()


def _due(watch: dict, now: float) -> bool:
    if not watch.get("enabled"):
        return False
    nxt = _next_poll_at.get(watch["id"], 0.0)
    return now >= nxt


def _mark_scheduled(watch_id: int, interval_s: int) -> None:
    _next_poll_at[watch_id] = time.time() + interval_s


def _prime_cursor(watch: dict, events: list[dict]) -> str | None:
    """First-poll behavior: don't alert on backlog. Return the newest event id
    to store as `last_event_id` so future polls only surface truly-new items.
    """
    if not events:
        return None
    # events[0] is the newest.
    eid = events[0].get("id")
    return eid if isinstance(eid, str) else None


def poll_one(watch: dict) -> dict:
    """Poll one watch. Returns a small stats dict for logging/CLI display.

    New events are inserted with alerted=0; the pet's tick loop will pick them
    up and fire the reaction.
    """
    interval = config.poll_interval_s()
    _mark_scheduled(watch["id"], interval)

    result = api.poll_repo(watch["owner"], watch["repo"], watch.get("etag"))
    stats = {
        "owner": watch["owner"], "repo": watch["repo"],
        "status": result.status, "new": 0, "seen": 0,
        "rate_remaining": result.rate_remaining, "error": result.error,
    }

    # Non-transient error → disable this watch and stop until user fixes it.
    if result.error:
        storage.update_cursor(watch["id"], last_error=result.error, etag=result.etag)
        return stats

    # 304: nothing new; just refresh last_checked (and any new etag if given).
    if result.status == 304:
        storage.update_cursor(watch["id"], etag=result.etag, last_error="")
        return stats

    # Rate-limited transient: back off but don't disable.
    if result.status == 403 and (result.rate_remaining or 0) == 0:
        global _rate_block_until
        _rate_block_until = float(result.rate_reset_ts or (time.time() + 60))
        return stats

    if result.status != 200:
        # transient / unexpected — leave cursor alone, retry next tick
        return stats

    events = result.events
    stats["seen"] = len(events)

    # First-ever poll for this watch: prime the cursor without alerting.
    if not watch.get("last_event_id"):
        newest = _prime_cursor(watch, events)
        storage.update_cursor(
            watch["id"], last_event_id=newest, etag=result.etag, last_error="",
        )
        return stats

    # Walk NEWER-than-cursor events, oldest first, so notifications arrive in
    # chronological order. `events` from GitHub is newest-first.
    cursor = watch["last_event_id"]
    fresh: list[dict] = []
    for ev in events:
        if ev.get("id") == cursor:
            break
        fresh.append(ev)
    fresh.reverse()

    newest_id = watch["last_event_id"]
    for ev in fresh:
        classified = classify.classify(ev)
        if not classified:
            continue
        et = classified["event_type"]
        # Store even non-alertable ones? No — classify() already filters to
        # types we're interested in. But respect the user's per-type toggle:
        # if disabled, we still record for the feed but set reaction='none'
        # so the alert loop skips it.
        reaction = classified["reaction"]
        if not config.alert_type_enabled(et):
            reaction = "none"
        else:
            # Per-repo per-type cooldown — stops WIP-push spam and rapid
            # re-review dinging. If we just fired an alert for this
            # (watch, event_type) inside the cooldown window, downgrade
            # to reaction='none' (recorded for the feed, no toast/sound).
            if _in_cooldown(watch["id"], et):
                reaction = "none"
            else:
                _mark_alerted(watch["id"], et)
        inserted = storage.record_event(
            watch_id=watch["id"],
            event_id=classified["event_id"],
            event_type=et,
            actor=classified.get("actor"),
            title=classified["title"],
            url=classified.get("url"),
            reaction=reaction,
            created_at=classified.get("created_at") or "",
        )
        if inserted:
            stats["new"] += 1
        newest_id = ev["id"]

    storage.update_cursor(
        watch["id"], last_event_id=newest_id, etag=result.etag, last_error="",
    )
    return stats


def poll_all_due() -> list[dict]:
    """Called from the pet's tick loop. Cheap when nothing is due."""
    if not config.enabled():
        return []
    now = time.time()
    if now < _rate_block_until:
        return []
    out = []
    for w in storage.list_watches(enabled_only=True):
        if not _due(w, now):
            continue
        try:
            out.append(poll_one(w))
        except Exception as exc:            # never let the watcher crash the pet
            storage.update_cursor(w["id"], last_error=f"poll crash: {exc}")
            out.append({"owner": w["owner"], "repo": w["repo"],
                        "status": 0, "new": 0, "seen": 0, "error": str(exc)})
    return out


def force_poll_all() -> list[dict]:
    """`claude-pet github check` entry point: ignore the schedule, poll everything."""
    _next_poll_at.clear()
    return poll_all_due()


def rate_block_until_ts() -> float:
    return _rate_block_until


def reset_state() -> None:
    """For tests: clear the module-level schedule + rate-block + cooldown."""
    global _rate_block_until
    _rate_block_until = 0.0
    _next_poll_at.clear()
    _last_alerted_at.clear()
