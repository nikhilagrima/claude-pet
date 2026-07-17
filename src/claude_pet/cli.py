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
    """The command hooks should use to invoke us.

    Always the quoted `python -m claude_pet` form: sys.executable is the
    interpreter that provably has this package importable RIGHT NOW.
    A PATH-resolved `claude-pet` entry point can rot (venv moved, PATH
    changed, pyenv switched) and is unquoted-fragile with spaces."""
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


def _running_version() -> str | None:
    """Version of the pet currently bound to :5050, or None if unreachable.
    Older pets (< 0.3.3) lack /version — treated as 'stale, unknown'."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:5050/version", timeout=1) as r:
            return json.loads(r.read()).get("version")
    except Exception:
        return None


def _ask_running_pet_to_quit() -> bool:
    """POST /shutdown to the running pet. True if it acknowledged."""
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://localhost:5050/shutdown", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            r.read()
        return True
    except Exception:
        return False


def cmd_start(args):
    if _is_running():
        from . import __version__
        running = _running_version()
        if running == __version__:
            print("[claude-pet] already running")
            return 0
        # A pet from an older install is holding the port — replace it so
        # upgrades take effect without the user hunting down stale processes.
        print(f"[claude-pet] running pet is v{running or 'unknown (pre-0.3.3)'}, "
              f"installed is v{__version__} — restarting…")
        if _ask_running_pet_to_quit():
            time.sleep(1.0)
        if _is_running():
            # /shutdown unsupported (old version) or ignored — fall back to stop.
            cmd_stop(args)
            time.sleep(1.0)
    # tempfile.gettempdir() is correct on every OS (TMPDIR/TEMP/TMP or
    # platform default). A hardcoded /tmp fallback broke Windows entirely.
    import tempfile
    log_path = Path(tempfile.gettempdir()) / "claude_pet.log"
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
    # Match both hyphen and underscore variants of the long-running pet
    # process ONLY. Deliberately no bare "-m claude_pet" pattern — that
    # would also match short-lived `hook` invocations and kill in-flight
    # event deliveries.
    for pattern in ("claude-pet run", "claude_pet run", "-m claude_pet run"):
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


def cmd_update(args):
    """Pull the latest release from GitHub and reinstall + restart the pet.

    Two paths:
    - editable install (git clone): `git pull` in the source tree, then
      pip re-install to catch new deps, then `claude-pet start` self-replaces.
    - regular install: `pip install --upgrade` from GitHub, then restart.

    Idempotent — running when already on latest just prints 'up to date'."""
    import subprocess

    # 1. Figure out what's installed vs latest.
    from . import __version__
    latest = None
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.github.com/repos/nikhilagrima/claude-pet/releases/latest",
            headers={"User-Agent": "claude-pet-update"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            latest = (json.loads(r.read()).get("tag_name") or "").lstrip("v")
    except Exception as e:
        print(f"[claude-pet] could not reach GitHub: {e}")

    print(f"[claude-pet] installed: {__version__}"
          + (f"    latest: {latest}" if latest else ""))
    if latest and latest == __version__ and not getattr(args, "force", False):
        print("[claude-pet] already up to date. Use --force to reinstall anyway.")
        return 0

    # 2. Detect editable install (pip's own metadata knows).
    import site
    from pathlib import Path
    editable_src = None
    try:
        import importlib.metadata as im
        dist = im.distribution("claude-pet")
        for f in (dist.files or []):
            if f.name == "direct_url.json":
                data = json.loads(Path(dist.locate_file(f)).read_text())
                if data.get("dir_info", {}).get("editable"):
                    url = data.get("url", "")
                    if url.startswith("file://"):
                        editable_src = url[len("file://"):]
                    break
    except Exception:
        pass

    py = _venv_python()
    if editable_src and (Path(editable_src) / ".git").exists():
        print(f"[claude-pet] editable install detected at {editable_src}")
        print(f"[claude-pet] git pulling…")
        r = subprocess.run(["git", "-C", editable_src, "pull", "--ff-only"],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            print("[claude-pet] git pull failed — resolve manually and re-run.")
            return 1
        print(f"[claude-pet] refreshing package (catches new deps)…")
        subprocess.run([py, "-m", "pip", "install", "-e", editable_src, "--quiet"],
                       check=False)
    else:
        print(f"[claude-pet] installing latest release from GitHub…")
        subprocess.run([
            py, "-m", "pip", "install", "--upgrade", "--quiet",
            "git+https://github.com/nikhilagrima/claude-pet.git",
        ], check=False)

    # 3. Restart — start's self-replace kicks the old process off :5050.
    print("[claude-pet] restarting pet…")
    cmd_start(args)
    print("[claude-pet] update complete.")
    return 0


def cmd_ergonomics(args):
    """`claude-pet ergonomics <sub>` — status / stats / break-now / snooze / on / off."""
    from .ergonomics import config as ergo_config
    from .ergonomics import tracker as ergo_tracker
    sub = getattr(args, "ergo_sub", None) or "status"
    cfg = ergo_config.load()
    if sub == "status":
        print(f"Ergonomics coach: {'ON' if cfg.get('enabled', True) else 'OFF'}")
        print(f"Quiet hours: {'on' if cfg.get('quiet_hours',{}).get('enabled') else 'off'}")
        print()
        print("Category thresholds (minutes) — [enabled]:")
        for cat in ("eyes", "neck", "wrists", "posture", "hydration"):
            m = cfg.get("intervals_min", {}).get(cat, "-")
            en = cfg.get("categories_enabled", {}).get(cat, True)
            elapsed_min = ergo_tracker.active_seconds_since_last(cat) / 60
            print(f"  {cat:10}  every {m} min   [{'x' if en else ' '}]   "
                  f"currently {elapsed_min:.1f} min into the window")
        return 0
    if sub == "stats":
        adh = ergo_tracker.adherence_last_n_days(7)
        streak = ergo_tracker.daily_streak()
        skipped = ergo_tracker.most_skipped_exercise()
        print(f"Last {adh['days']} days: {adh['completed']}/{adh['total']} breaks "
              f"({adh['adherence']*100:.0f}% adherence)")
        print(f"Current streak: {streak} day{'s' if streak != 1 else ''}")
        if skipped:
            print(f"Most skipped: {skipped[0]} ({skipped[1]}x)")
        today = ergo_tracker.today_breaks()
        print(f"Today: {len(today)} break(s)")
        for b in today[:10]:
            mark = "✓" if b["completed"] else "✗"
            print(f"  {mark} {b['ts']}  {b['category']:10}  {b['exercise']}")
        return 0
    if sub == "break-now":
        # Ask the running pet to open a break — dedicated /break endpoint that
        # the pet's polling loop drains within the next ~110 ms tick.
        import urllib.request
        # Optional 3rd arg = specific exercise slug (e.g. `break-now eye-break`).
        payload = {}
        slug = getattr(args, "snooze_min", None)   # positional arg reused
        if isinstance(slug, str):
            payload["slug"] = slug
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    "http://localhost:5050/break",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                ), timeout=1,
            )
            print("[claude-pet] break request queued — overlay opens momentarily.")
        except Exception:
            print("[claude-pet] pet server not responding; start it first.")
        return 0
    if sub == "snooze":
        # e.g. `claude-pet ergonomics snooze 30` → 30 minutes.
        minutes = int(getattr(args, "snooze_min", 30))
        # Snoozing is enforced by the running pet's in-memory flag; without a
        # daemon we can only persist it as a "snoozed_until" wall-clock stamp.
        import time as _t
        until = _t.time() + minutes * 60
        cfg["_snoozed_until"] = until
        ergo_config.save(cfg)
        print(f"[claude-pet] snoozed for {minutes} min "
              f"(until {_t.strftime('%H:%M', _t.localtime(until))}).")
        return 0
    if sub in ("on", "off"):
        cfg["enabled"] = (sub == "on")
        ergo_config.save(cfg)
        print(f"[claude-pet] ergonomics coach: {sub.upper()}")
        return 0
    if sub == "reset":
        ergo_tracker.reset_all()
        print("[claude-pet] ergonomics history wiped.")
        return 0
    print(f"unknown subcommand: {sub}")
    return 1


def _parse_owner_repo(spec: str | None) -> tuple[str, str] | None:
    if not spec or "/" not in spec:
        return None
    owner, _, repo = spec.partition("/")
    owner, repo = owner.strip(), repo.strip()
    if not owner or not repo:
        return None
    return owner, repo


def _fmt_ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso)
        delta = (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return iso
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def cmd_github(args):
    """`claude-pet github <sub>` — watch/unwatch/list/events/check/token/enable/disable."""
    from .github_watch import config as gh_config, storage, watcher
    sub = getattr(args, "gh_sub", None) or "list"
    arg = getattr(args, "gh_arg", None)

    if sub == "list":
        rows = storage.list_watches()
        if not rows:
            print("No repos watched yet. Add one with:  claude-pet github watch owner/repo")
            return 0
        print(f"{'REPO':<40} {'ENABLED':<8} {'LAST CHECK':<14} {'STATUS'}")
        for r in rows:
            slug = f"{r['owner']}/{r['repo']}"
            enabled = "yes" if r["enabled"] else "no"
            checked = _fmt_ago(r["last_checked"])
            status = r["last_error"] or "ok"
            print(f"{slug:<40} {enabled:<8} {checked:<14} {status}")
        return 0

    if sub == "watch":
        or_repo = _parse_owner_repo(arg)
        if not or_repo:
            print("usage: claude-pet github watch owner/repo")
            return 2
        w = storage.add_watch(*or_repo)
        print(f"[claude-pet] watching {w['owner']}/{w['repo']} "
              f"(id={w['id']}). Poll interval: {gh_config.poll_interval_s()}s.")
        return 0

    if sub == "unwatch":
        or_repo = _parse_owner_repo(arg)
        if not or_repo:
            print("usage: claude-pet github unwatch owner/repo")
            return 2
        ok = storage.remove_watch(*or_repo)
        print(f"[claude-pet] {'removed' if ok else 'no such watch'}: {arg}")
        return 0 if ok else 1

    if sub in ("enable", "disable"):
        or_repo = _parse_owner_repo(arg)
        if not or_repo:
            print(f"usage: claude-pet github {sub} owner/repo")
            return 2
        ok = storage.set_enabled(*or_repo, sub == "enable")
        print(f"[claude-pet] {'ok' if ok else 'no such watch'}: {arg} {sub}d")
        return 0 if ok else 1

    if sub == "events":
        limit = getattr(args, "limit", 20)
        rows = storage.recent_events(limit=limit)
        if not rows:
            print("No events yet. Try:  claude-pet github check")
            return 0
        for r in rows:
            mark = {"success": "+", "error": "!", "curious": "?", "none": "."}.get(
                r["reaction"], " "
            )
            slug = f"{r['owner']}/{r['repo']}"
            print(f"[{mark}] {_fmt_ago(r['seen_at']):>8}  {slug:<28} {r['title']}")
            if r.get("url"):
                print(f"     -> {r['url']}")
        return 0

    if sub == "check":
        stats = watcher.force_poll_all()
        if not stats:
            if not gh_config.enabled():
                print("[claude-pet] GitHub watcher is disabled in config.")
                return 0
            print("[claude-pet] no repos to poll. Add one with:  "
                  "claude-pet github watch owner/repo")
            return 0
        for s in stats:
            err = s.get("error") or "ok"
            print(f"  {s['owner']}/{s['repo']:<30} "
                  f"status={s['status']} seen={s['seen']} new={s['new']} "
                  f"rate={s.get('rate_remaining')}  {err}")
        return 0

    if sub == "token":
        if getattr(args, "remove", False):
            gh_config.set_token(None)
            print("[claude-pet] stored GitHub token cleared.")
            return 0
        if not arg:
            t = gh_config.token()
            if t:
                print(f"[claude-pet] token set ({'env override' if os.environ.get('CLAUDE_PET_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN') else 'stored'}). Last 4 chars: …{t[-4:]}")
            else:
                print("[claude-pet] no token set. Pass one:  claude-pet github token <PAT>")
            return 0
        gh_config.set_token(arg)
        print("[claude-pet] token stored in ~/.claude/claude-pet/config.json (chmod 600).")
        return 0

    print(f"unknown github subcommand: {sub}")
    return 1


def cmd_forget(args):
    """Delete every memory row for a project (CLI counterpart of the UI's
    'Delete selected project from memory' button)."""
    target = args.path or memory.current_project()
    counts = memory.delete_project(target)
    total = sum(v for k, v in counts.items())
    if total == 0:
        print(f"[claude-pet] no memory found for {target}")
        return 0
    parts = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    print(f"[claude-pet] forgot {target} ({parts})")
    return 0


def cmd_note(args):
    text = " ".join(args.text).strip()
    if not text:
        print("[claude-pet] refusing to save an empty note.", file=sys.stderr)
        return 1
    memory.add_note(text)
    print(f"[claude-pet] noted: {text}")
    return 0


def cmd_doctor(args):
    """Diagnose common install problems and self-heal where possible.

    Checks:
      1. Is the Python each hook points to actually executable?
      2. Does that Python have the `claude_pet` module installed?
      3. Is the pet-state server responding on :5050?
      4. Does the pet process exist?
    Re-runs install-hooks with sys.executable if any hook path is broken."""
    import subprocess

    settings = _settings_path()
    ok = True
    issues: list[str] = []

    print(f"[doctor] settings file: {settings}")
    if not settings.exists():
        print("  ✗ settings.json not found — run: claude-pet install-hooks")
        return 1

    try:
        data = json.loads(settings.read_text())
    except Exception as e:
        print(f"  ✗ settings.json is unparseable: {e}")
        return 1

    hooks = data.get("hooks", {})
    if not hooks:
        print("  ✗ no hooks configured — run: claude-pet install-hooks")
        return 1

    # Extract every distinct Python path our hooks reference.
    broken_paths = set()
    all_pet_paths = set()
    for event, entries in hooks.items():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if "claude_pet" not in cmd and "claude-pet" not in cmd:
                    continue
                # Extract the binary path. Two forms exist in the wild:
                #   "\"/path with spaces/python\" -m claude_pet hook X"  (quoted)
                #   "/usr/local/bin/claude-pet hook X"                    (bare)
                if cmd.startswith('"'):
                    end = cmd.find('"', 1)
                    path = cmd[1:end]
                else:
                    path = cmd.split()[0] if cmd.split() else ""
                if not path:
                    continue
                all_pet_paths.add(path)
                if not shutil.which(path) and not os.path.isfile(path):
                    broken_paths.add(path)

    if all_pet_paths:
        print("[doctor] hook binaries referenced:")
        for p in sorted(all_pet_paths):
            mark = "✓" if p not in broken_paths else "✗"
            print(f"  {mark} {p}")

    if broken_paths:
        ok = False
        issues.append("broken hook binary paths (see ✗ above)")

    # Is our sys.executable a working claude-pet install?
    try:
        subprocess.check_output(
            [_venv_python(), "-c", "import claude_pet"], stderr=subprocess.STDOUT,
        )
        print(f"[doctor] current interpreter has claude-pet: ✓ {_venv_python()}")
    except Exception as e:
        ok = False
        print(f"[doctor] current interpreter can't import claude_pet: ✗ {e}")
        issues.append("current interpreter is missing the claude_pet package")

    # Is the server responding?
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:5050/state", timeout=1) as r:
            r.read()
        print("[doctor] pet server on :5050: ✓ responding")
    except Exception:
        print("[doctor] pet server on :5050: — not running (start with: claude-pet start)")

    if broken_paths:
        print()
        print("[doctor] re-wiring hooks to point at:", _venv_python())
        # Reinstall using the current interpreter's path.
        cmd_install_hooks(args)
        print("[doctor] ✓ hooks re-wired.")
        ok = True

    if ok:
        print("[doctor] all clear.")
        return 0
    else:
        print("[doctor] issues remaining:")
        for i in issues:
            print(f"  - {i}")
        return 1


def cmd_context(args):
    """Print project context in a form suitable for pasting into a new Claude
    Code session or letting a SessionStart hook inject it into the model.

    Uses the ranked ≤N-token builder from Phase 3."""
    from . import context as ctx
    budget = args.budget if hasattr(args, "budget") and args.budget else ctx.DEFAULT_TOKENS
    block = ctx.build_context(token_budget=budget)
    if args.json:
        import json as _json
        print(_json.dumps({
            "project": memory.current_project(),
            "budget_tokens": budget,
            "actual_tokens": ctx.estimate_tokens(block),
            "context": block,
        }, indent=2))
    else:
        print(block)
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
    sub.add_parser("doctor", help="diagnose install + auto-fix broken hook paths")

    update_p = sub.add_parser("update", help="pull latest release from GitHub, reinstall, restart")
    update_p.add_argument("--force", action="store_true",
                          help="reinstall even if already on latest version")
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

    forget_p = sub.add_parser(
        "forget",
        help="delete every memory row for a project (also available via UI)",
    )
    forget_p.add_argument("--path", help="project path to forget (default: current)")

    ergo_p = sub.add_parser(
        "ergonomics",
        help="ergonomics coach: status | stats | break-now | snooze N | on | off | reset",
    )
    ergo_p.add_argument("ergo_sub", nargs="?", default="status",
                        choices=["status", "stats", "break-now", "snooze",
                                 "on", "off", "reset"])
    ergo_p.add_argument("snooze_min", nargs="?", type=int, default=30,
                        help="minutes to snooze (only for 'snooze')")

    gh_p = sub.add_parser(
        "github",
        help="watch GitHub repos for commits, PRs, reviews, releases, deploys",
    )
    gh_p.add_argument(
        "gh_sub", nargs="?", default="list",
        choices=["watch", "unwatch", "list", "events", "check", "token", "enable", "disable"],
    )
    gh_p.add_argument(
        "gh_arg", nargs="?", default=None,
        help="owner/repo for watch|unwatch|enable|disable; token value for token; nothing for list|events|check",
    )
    gh_p.add_argument("--remove", action="store_true",
                      help="only for 'token': clear the stored PAT")
    gh_p.add_argument("--limit", type=int, default=20,
                      help="only for 'events': max rows to show")

    ctx_p = sub.add_parser(
        "context",
        help="print current project's saved context (for pasting into a new session)",
    )
    ctx_p.add_argument("--budget", type=int, default=None,
                       help="token budget (default 800; chars≈budget×4)")
    ctx_p.add_argument("--json", action="store_true",
                       help="wrap output as JSON with token counts")

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
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "memory":
        return cmd_memory(args)
    if args.cmd == "note":
        return cmd_note(args)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "forget":
        return cmd_forget(args)
    if args.cmd == "ergonomics":
        return cmd_ergonomics(args)
    if args.cmd == "update":
        return cmd_update(args)
    if args.cmd == "github":
        return cmd_github(args)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
