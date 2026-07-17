"""Pure event → (title, url, reaction) mapping.

Kept separate from the HTTP layer so it can be exercised with fixture events
in tests without any network.
"""

from __future__ import annotations

from typing import Any


# Which event types can trigger the pet at all (subject to per-type user toggle).
_ALERTABLE = {
    "PushEvent",
    "PullRequestEvent",
    "PullRequestReviewEvent",
    "ReleaseEvent",
    "IssuesEvent",
    "WorkflowRunEvent",
    "DeploymentStatusEvent",
}


def _actor(event: dict[str, Any]) -> str | None:
    a = event.get("actor") or {}
    return a.get("login") or a.get("display_login") or None


def _repo_full(event: dict[str, Any]) -> str:
    r = event.get("repo") or {}
    name = r.get("name")
    if isinstance(name, str) and "/" in name:
        return name
    return ""


def _html_url(event: dict[str, Any], suffix: str = "") -> str | None:
    full = _repo_full(event)
    if not full:
        return None
    return f"https://github.com/{full}{suffix}"


def classify(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return `{event_id, event_type, actor, title, url, reaction, created_at}`
    or None if this event should be stored-but-silent (reaction='none') skipped.

    Reactions map to sound + pet emotion:
      success → done beeps + success eye
      error   → error thud + error eye
      curious → attention beeps + curious eye
      none    → don't alert (but still record for the feed)
    """
    et = event.get("type")
    if not isinstance(et, str):
        return None
    eid = event.get("id")
    if not isinstance(eid, str) or not eid:
        return None

    actor = _actor(event)
    created = event.get("created_at") or ""
    payload = event.get("payload") or {}
    base: dict[str, Any] = {
        "event_id": eid,
        "event_type": et,
        "actor": actor,
        "created_at": created,
        "reaction": "none",
        "url": _html_url(event),
        "title": "",
    }

    if et == "PushEvent":
        n = len(payload.get("commits") or [])
        ref = payload.get("ref", "").split("/")[-1] or "?"
        base["title"] = f"{n} new commit{'s' if n != 1 else ''} on {ref}" + (
            f" by @{actor}" if actor else "")
        base["reaction"] = "curious"
        return base

    if et == "PullRequestEvent":
        action = payload.get("action")
        pr = payload.get("pull_request") or {}
        num = pr.get("number") or payload.get("number")
        title = pr.get("title") or ""
        base["url"] = pr.get("html_url") or base["url"]
        if action == "opened":
            base["title"] = f"PR #{num} opened: {title}"
            base["reaction"] = "curious"
        elif action == "closed":
            if pr.get("merged"):
                base["title"] = f"PR #{num} merged: {title}"
                base["reaction"] = "success"
            else:
                base["title"] = f"PR #{num} closed without merge: {title}"
                base["reaction"] = "error"
        elif action == "reopened":
            base["title"] = f"PR #{num} reopened: {title}"
            base["reaction"] = "curious"
        else:
            return None      # edits, sync, etc. — too noisy
        return base

    if et == "PullRequestReviewEvent":
        pr = payload.get("pull_request") or {}
        review = payload.get("review") or {}
        state = (review.get("state") or "").lower()
        num = pr.get("number")
        base["url"] = review.get("html_url") or pr.get("html_url") or base["url"]
        who = f"@{actor}" if actor else "someone"
        if state == "approved":
            base["title"] = f"{who} approved PR #{num}"
            base["reaction"] = "success"
        elif state == "changes_requested":
            base["title"] = f"{who} requested changes on PR #{num}"
            base["reaction"] = "error"
        elif state == "commented":
            base["title"] = f"{who} reviewed PR #{num}"
            base["reaction"] = "curious"
        else:
            return None
        return base

    if et == "ReleaseEvent":
        if payload.get("action") != "published":
            return None
        rel = payload.get("release") or {}
        tag = rel.get("tag_name") or rel.get("name") or "release"
        base["url"] = rel.get("html_url") or base["url"]
        base["title"] = f"Release {tag} published"
        base["reaction"] = "success"
        return base

    if et == "IssuesEvent":
        if payload.get("action") != "opened":
            return None      # closes/reopens are quiet by default
        issue = payload.get("issue") or {}
        num = issue.get("number")
        title = issue.get("title") or ""
        base["url"] = issue.get("html_url") or base["url"]
        base["title"] = f"Issue #{num} opened: {title}"
        base["reaction"] = "curious"
        return base

    if et == "WorkflowRunEvent":
        if payload.get("action") != "completed":
            return None
        run = payload.get("workflow_run") or {}
        name = run.get("name") or "workflow"
        concl = (run.get("conclusion") or "").lower()
        base["url"] = run.get("html_url") or base["url"]
        if concl == "success":
            base["title"] = f"CI: {name} passed"
            base["reaction"] = "success"
        elif concl in ("failure", "timed_out", "startup_failure"):
            base["title"] = f"CI: {name} failed ({concl})"
            base["reaction"] = "error"
        elif concl == "cancelled":
            base["title"] = f"CI: {name} cancelled"
            base["reaction"] = "curious"
        else:
            return None
        return base

    if et == "DeploymentStatusEvent":
        ds = payload.get("deployment_status") or {}
        dep = payload.get("deployment") or {}
        state = (ds.get("state") or "").lower()
        env = dep.get("environment") or ds.get("environment") or "env"
        base["url"] = ds.get("target_url") or dep.get("url") or base["url"]
        if state == "success":
            base["title"] = f"Deploy to {env} succeeded"
            base["reaction"] = "success"
        elif state in ("failure", "error"):
            base["title"] = f"Deploy to {env} failed"
            base["reaction"] = "error"
        elif state == "in_progress":
            base["title"] = f"Deploy to {env} started"
            base["reaction"] = "curious"
        else:
            return None
        return base

    # Everything else: ignored entirely (not stored, not shown).
    return None


def alertable_types() -> set[str]:
    return set(_ALERTABLE)
