"""Claude Code hook → desktop pet state + project memory."""

import json
import os
import sys

import requests

from . import memory
from . import distill
from . import context as ctx
from . import skills
from .ergonomics import tracker as ergo_tracker

PET_URL = "http://localhost:5050/state"


def notify(status, event=None):
    try:
        requests.post(PET_URL, json={"status": status, "event": event}, timeout=0.5)
    except Exception:
        pass


def classify(event, tool):
    e = (event or "").lower()
    if e in ("pretooluse", "onpretooluse", "onworking"):
        t = (tool or "").lower()
        if t in ("read", "glob", "grep"):
            return "reading"
        if t in ("write", "edit", "notebookedit"):
            return "writing"
        if t == "bash":
            return "running"
        if t in ("webfetch", "websearch"):
            return "curious"
        if t in ("task", "agent"):
            return "thinking"
        return "working"
    if e in ("posttooluseFailure".lower(), "onerror", "stopfailure"):
        return "error"
    if e in ("stop", "ondone"):
        return "success"
    if e in ("userpromptsubmit", "onprompt", "onthinking"):
        return "thinking"
    if e == "notification":
        return "curious"
    if e in ("precompact", "postcompact"):
        return "sleeping"
    if e == "sessionstart":
        return "proud"
    if e == "sessionend":
        return "sleeping"
    return None


def _remember(event: str, tool: str | None, project_path: str | None) -> None:
    """Persist facts about this event into the SQLite memory. Best-effort —
    any failure is silent so we never break Claude Code's tool call."""
    try:
        e = event.lower()
        if e == "sessionstart":
            memory.record_session_start(project_path)
            # Also ingest any Understand-Anything graph shipped with the project.
            distill.ingest_ua_dir_if_present(project_path or ".")
        elif e in ("pretooluse", "onpretooluse", "onworking"):
            if tool:
                memory.record_tool_use(tool, project_path)
            # Every tool call is genuine activity — feeds the ergonomics
            # counters. Best-effort; failures never break the hook.
            try:
                ergo_tracker.mark_activity()
            except Exception:
                pass
        elif e in ("userpromptsubmit", "onprompt", "onthinking"):
            try:
                ergo_tracker.mark_activity()
            except Exception:
                pass
        elif e in ("stop", "ondone"):
            memory.record_success(project_path)
            # Distill the just-ended session into graph nodes.
            distill.distill_session(project_path or ".")
            # Then promote any nodes that crossed the reinforcement threshold.
            skills.scan_and_promote(project_path or ".")
        elif e in ("posttooluseFailure".lower(), "onerror", "stopfailure"):
            memory.record_error(project_path)
    except Exception:
        pass


def _emit_session_context(project_path: str) -> None:
    """Print JSON that Claude Code's SessionStart hook understands as an
    `additionalContext` injection. Uses the ≤800-token context builder
    (Phase 3) so it's ranked, deterministic, and budget-enforced."""
    try:
        summary = memory.project_summary(project_path)
        if not summary.get("known"):
            return
        totals = summary.get("totals", {})
        # Only inject if there's actually meaningful history to share.
        if totals.get("sessions", 0) < 1 and not summary.get("notes"):
            return
        block = ctx.build_context(project_path)
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": block,
            }
        }
        print(json.dumps(payload))
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        return 0
    event = sys.argv[1]
    tool = None
    project_path = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
                tool = data.get("tool_name")
                # Some Claude Code payloads include the project path explicitly.
                project_path = data.get("cwd") or data.get("project_dir") or project_path
        except Exception:
            pass
    _remember(event, tool, project_path)
    emotion = classify(event, tool)
    if emotion:
        notify(emotion, event)
    # On SessionStart, inject the project's saved context into the model.
    if event.lower() == "sessionstart":
        _emit_session_context(project_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
