"""Claude Code hook → desktop pet state + project memory."""

import json
import os
import sys

import requests

from . import memory

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
        elif e in ("pretooluse", "onpretooluse", "onworking"):
            if tool:
                memory.record_tool_use(tool, project_path)
        elif e in ("stop", "ondone"):
            memory.record_success(project_path)
        elif e in ("posttooluseFailure".lower(), "onerror", "stopfailure"):
            memory.record_error(project_path)
    except Exception:
        pass


def _emit_session_context(project_path: str) -> None:
    """Print JSON that Claude Code's SessionStart hook understands as an
    `additionalContext` injection. Only emit if this project has real history
    — no point injecting 'no history yet' on the model's first turn."""
    try:
        summary = memory.project_summary(project_path)
        if not summary.get("known"):
            return
        totals = summary.get("totals", {})
        # Only inject if there's actually meaningful history to share.
        if totals.get("sessions", 0) < 1 and not summary.get("notes"):
            return
        context = memory.format_context(project_path)
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "Claude Pet remembered this project. Prior context:\n\n"
                    f"{context}\n"
                ),
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
