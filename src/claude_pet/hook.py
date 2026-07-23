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


def _read_pet_token() -> str:
    """Read the server's shared secret from ~/.claude/claude-pet/server.token.
    The server writes this on first boot; hooks send it as X-Pet-Token."""
    from pathlib import Path
    p = Path.home() / ".claude" / "claude-pet" / "server.token"
    try:
        return p.read_text().strip() if p.exists() else ""
    except Exception:
        return ""


def notify(status, event=None):
    # Non-blocking-ish timeout — 200ms so a hung server never blocks Claude
    # Code's tool execution. Was 500ms; hook.py:20 audit finding B2.
    try:
        requests.post(
            PET_URL,
            json={"status": status, "event": event},
            headers={"X-Pet-Token": _read_pet_token()},
            timeout=0.2,
        )
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
    (Phase 3) so it's ranked, deterministic, and budget-enforced.

    Emits whenever the built block is non-empty. The block always contains
    safety rules (never trimmed) even for brand-new projects with no graph
    yet — that's exactly the case where the safety scaffolding matters
    most. The old sessions/notes guard was suppressing those on cold starts.
    """
    from .errors import log_exception
    try:
        block = ctx.build_context(project_path)
        # Fitness bridge — if a weekly adjustment is pending (Sunday, no
        # note yet for this ISO week), append a compact ask so Claude Code
        # writes the note to ~/.claude/claude-pet/fitness_note.txt. The
        # pet's fitness overlay picks it up next tick and shows it once.
        # Additive: never required for the pet's core memory work.
        try:
            from .fitness import coach as fcoach
            # 1. Weekly adjustment note (Sundays only, once per ISO week)
            if fcoach.weekly_adjustment_pending():
                block = (block or "") + "\n\n" + fcoach.build_weekly_adjustment_context()
                fcoach.mark_week_note_generated()
            # 2. Every-session fitness snapshot + gap-aware ask (data-only
            # when the user is on-track; asks Claude Code to WebSearch and
            # write suggestions when body parts are missed or trend stalled).
            fitness_ctx = fcoach.build_fitness_session_context()
            if fitness_ctx:
                block = (block or "") + "\n" + fitness_ctx
        except Exception:
            log_exception("hook.fitness_bridge")
        if not block or not block.strip():
            return
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": block,
            }
        }
        print(json.dumps(payload))
    except Exception:
        log_exception("hook._emit_session_context")


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
