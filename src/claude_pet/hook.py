"""Claude Code hook → desktop pet state."""

import json
import sys

import requests

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


def main():
    if len(sys.argv) < 2:
        return 0
    event = sys.argv[1]
    tool = None
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                tool = json.loads(raw).get("tool_name")
        except Exception:
            pass
    emotion = classify(event, tool)
    if emotion:
        notify(emotion, event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
