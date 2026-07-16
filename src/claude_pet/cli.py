"""CLI entry point for Claude Pet."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import memory


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _venv_python() -> str:
    return sys.executable


def _cli_path() -> str:
    entry = shutil.which("claude-pet")
    if entry:
        return entry
    return f'"{_venv_python()}" -m claude_pet'


def cmd_run(args):
    from .server import app as flask_app
    from . import app as pet_app

    def serve():
        flask_app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.3)
    pet_app.main(show_in_dock=getattr(args, "show_in_dock", False))


def cmd_start(args):
    if _is_running():
        print("[claude-pet] already running")
        return 0
    log_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    log_path = log_dir / "claude_pet.log"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x00000008 | 0x00000200
    run_argv = [_venv_python(), "-m", "claude_pet", "run"]
    if getattr(args, "show_in_dock", False):
        run_argv.append("--show-in-dock")
    with open(log_path, "ab") as log:
        subprocess.Popen(
            run_argv,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
            creationflags=creationflags,
            close_fds=True,
        )
    print(f"[claude-pet] started; logs at {log_path}")
    return 0


def cmd_stop(args):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "python.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[claude-pet] requested taskkill (may affect other python.exe)")
        return 0
    killed = 0
    # Match both hyphen and underscore variants of the process name.
    for pattern in ("claude-pet run", "claude_pet run", "-m claude_pet"):
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True, text=True,
            )
            for pid in result.stdout.strip().splitlines():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    killed += 1
                except Exception:
                    pass
        except FileNotFoundError:
            pass
    print(f"[claude-pet] killed {killed} processes")
    return 0


def cmd_hook(args):
    from . import hook
    sys.argv = ["hook"] + args.hook_args
    return hook.main()


def cmd_memory(args):
    """Show project memory. Default: current project. --all lists everything."""
    if args.all:
        rows = memory.list_projects()
        if not rows:
            print("[claude-pet] no projects remembered yet.")
            return 0
        print(f"{'last seen':<28} {'sessions':>8}  {'tool calls':>10}  path")
        print("-" * 100)
        for r in rows:
            print(f"{r['last_seen']:<28} {r['session_count']:>8}  {r['tool_calls']:>10}  {r['path']}")
        return 0
    if args.path:
        target = args.path
    else:
        target = None  # current project
    if args.json:
        print(memory.as_json(target))
    else:
        print(memory.format_context(target))
    return 0


def cmd_note(args):
    text = " ".join(args.text).strip()
    if not text:
        print("[claude-pet] refusing to save an empty note.", file=sys.stderr)
        return 1
    memory.add_note(text)
    print(f"[claude-pet] noted: {text}")
    return 0


def cmd_context(args):
    """Print project context in a form suitable for pasting into a new Claude
    Code session or letting a SessionStart hook inject it into the model."""
    print(memory.format_context())
    return 0


def cmd_install_hooks(args):
    settings = _settings_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text())
        except Exception:
            print(f"[claude-pet] could not parse {settings} — aborting")
            return 1
    hooks = data.setdefault("hooks", {})
    cli = _cli_path()

    def hook_entry(event):
        return {
            "hooks": [
                {
                    "type": "command",
                    "command": f'{cli} hook {event}',
                    "async": True,
                    "timeout": 2,
                }
            ]
        }

    events = (
        "PreToolUse", "PostToolUseFailure", "UserPromptSubmit",
        "Notification", "SessionStart", "Stop",
    )
    for ev in events:
        entries = hooks.setdefault(ev, [])
        entries[:] = [e for e in entries
                      if not any("claude-pet" in (h.get("command") or "") or
                                 "claude_pet" in (h.get("command") or "")
                                 for h in e.get("hooks", []))]
        entries.append(hook_entry(ev))

    start_entry = {
        "hooks": [
            {
                "type": "command",
                "command": f'{cli} start',
                "async": True,
                "timeout": 5,
            }
        ]
    }
    hooks["SessionStart"].insert(0, start_entry)

    settings.write_text(json.dumps(data, indent=2))
    print(f"[claude-pet] hooks installed → {settings}")
    print("[claude-pet] hooks activate on the next Claude Code session.")
    return 0


def cmd_uninstall_hooks(args):
    settings = _settings_path()
    if not settings.exists():
        print(f"[claude-pet] no settings file at {settings}")
        return 0
    try:
        data = json.loads(settings.read_text())
    except Exception:
        return 1
    hooks = data.get("hooks", {})
    for ev, entries in list(hooks.items()):
        entries[:] = [
            e for e in entries
            if not any("claude-pet" in (h.get("command") or "") or
                       "claude_pet" in (h.get("command") or "")
                       for h in e.get("hooks", []))
        ]
        if not entries:
            del hooks[ev]
    settings.write_text(json.dumps(data, indent=2))
    print(f"[claude-pet] hooks removed from {settings}")
    return 0


def _is_running():
    """True if a pet server is already bound to port 5050.

    Port-check is cross-platform and reliable — pgrep-based checks were
    fragile because the process name can be 'claude-pet run', 'claude_pet run',
    'python -m claude_pet run', or a bundled .app path depending on how the
    pet was launched.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", 5050)) == 0
    except Exception:
        return False
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(prog="claude-pet")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run pet + server in foreground")
    run_p.add_argument("--show-in-dock", action="store_true",
                       help="macOS only: show the pet icon in the Dock / Cmd-Tab")

    start_p = sub.add_parser("start", help="start in background (idempotent)")
    start_p.add_argument("--show-in-dock", action="store_true",
                         help="macOS only: show the pet icon in the Dock / Cmd-Tab")

    sub.add_parser("stop", help="kill running pet/server")
    sub.add_parser("install-hooks", help="wire Claude Code hooks")
    sub.add_parser("uninstall-hooks", help="remove Claude Code hooks")
    h = sub.add_parser("hook", help="internal: handle a hook event")
    h.add_argument("hook_args", nargs="*")

    mem_p = sub.add_parser(
        "memory",
        help="show project memory (default: current project; --all for a list)",
    )
    mem_p.add_argument("--all", action="store_true", help="list every remembered project")
    mem_p.add_argument("--path", help="specific project path to show")
    mem_p.add_argument("--json", action="store_true", help="raw JSON output")

    note_p = sub.add_parser("note", help="attach a free-form note to the current project")
    note_p.add_argument("text", nargs="+", help="note text (unquoted words are fine)")

    sub.add_parser(
        "context",
        help="print current project's saved context (for pasting into a new session)",
    )

    args = parser.parse_args()
    if args.cmd == "run":
        return cmd_run(args) or 0
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "hook":
        return cmd_hook(args)
    if args.cmd == "install-hooks":
        return cmd_install_hooks(args)
    if args.cmd == "uninstall-hooks":
        return cmd_uninstall_hooks(args)
    if args.cmd == "memory":
        return cmd_memory(args)
    if args.cmd == "note":
        return cmd_note(args)
    if args.cmd == "context":
        return cmd_context(args)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
