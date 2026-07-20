import os
import secrets
import signal
import threading
from functools import wraps
from pathlib import Path

from flask import Flask, request, jsonify
from flask_cors import CORS

from . import __version__

app = Flask(__name__)
CORS(app)

state = {"status": "idle", "last_event": None}

# Break request queue — a POST /break enqueues; the running pet's polling
# loop drains it and opens a Qt overlay. Simpler than adding an IPC channel;
# reuses the existing Flask + polling pipeline the pet already runs.
_pending_break = {"queued": False, "category": None, "slug": None}


# ------ shared-secret token for write endpoints ------------------------------
# The pet's Flask server binds to 127.0.0.1 so remote hosts can't reach it,
# but ANY local process (a misbehaved dev tool, curious script, malware)
# could POST /shutdown and kill the pet. Guard write endpoints with a token
# written to ~/.claude/claude-pet/server.token (chmod 600). Read endpoints
# (/version, /state GET, /window-status) stay open for observability.
_TOKEN_PATH = Path.home() / ".claude" / "claude-pet" / "server.token"


def _read_or_create_token() -> str:
    """Return the shared secret, generating it on first boot. Chmod 600 on
    POSIX so nobody but the user can read it."""
    try:
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _TOKEN_PATH.exists():
            tok = _TOKEN_PATH.read_text().strip()
            if len(tok) >= 32:
                return tok
        tok = secrets.token_urlsafe(32)
        _TOKEN_PATH.write_text(tok)
        try:
            os.chmod(_TOKEN_PATH, 0o600)
        except Exception:
            pass
        return tok
    except Exception:
        # If the FS is unavailable (TCC block), fall back to an in-memory
        # token that at least changes every process restart.
        return secrets.token_urlsafe(32)


_SERVER_TOKEN = _read_or_create_token()


def require_token(fn):
    """Decorator — require X-Pet-Token header for write endpoints.

    Reads the CURRENT on-disk token every check so `claude-pet` CLI can
    read the same file and supply the header without a restart dance.
    """
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        supplied = request.headers.get("X-Pet-Token", "")
        try:
            current = _TOKEN_PATH.read_text().strip() if _TOKEN_PATH.exists() else _SERVER_TOKEN
        except Exception:
            current = _SERVER_TOKEN
        # constant-time compare — token isn't sensitive-key-grade but still
        # avoids handing out a length-timing oracle for free.
        if not supplied or not secrets.compare_digest(supplied, current):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return _wrapped


def server_token() -> str:
    """Read the current token from disk (fallback to in-memory boot value).
    Used by claude-pet CLI to send the X-Pet-Token header on write calls."""
    try:
        if _TOKEN_PATH.exists():
            return _TOKEN_PATH.read_text().strip()
    except Exception:
        pass
    return _SERVER_TOKEN


@app.route("/version", methods=["GET"])
def get_version():
    """Lets `claude-pet start` detect a stale running pet after an upgrade."""
    return jsonify({"version": __version__, "pid": os.getpid()})


def _windows_window_status():
    """Report whether the pet's HWND is currently in the TOPMOST z-order set.
    HWND_TOPMOST is the Windows equivalent of macOS level=1500."""
    try:
        import ctypes
        WS_EX_TOPMOST = 0x00000008
        GWL_EXSTYLE = -20
        u32 = ctypes.windll.user32
        # Enumerate top-level windows and find ours by process id
        pid = os.getpid()
        result = {"pid": pid, "windows": [], "pin_healthy": False}
        found_any = False
        healthy = True

        def _enum_cb(hwnd, _):
            nonlocal found_any, healthy
            got_pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(got_pid))
            if got_pid.value != pid:
                return True
            ex_style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            is_topmost = bool(ex_style & WS_EX_TOPMOST)
            visible = bool(u32.IsWindowVisible(hwnd))
            if visible:
                found_any = True
                if not is_topmost:
                    healthy = False
            result["windows"].append({
                "hwnd": hwnd, "topmost": is_topmost, "visible": visible,
            })
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
        )
        u32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        result["pin_healthy"] = found_any and healthy
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)})


def _linux_window_status():
    """Best-effort report on Linux. On X11 we check for _NET_WM_STATE_ABOVE;
    on Wayland the compositor typically hides window state from us."""
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    display = "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"
    return jsonify({
        "platform": "linux",
        "session_type": session,
        "display_server": display,
        "note": ("Wayland compositors control window stacking; "
                 "always-on-top may be limited") if display == "wayland"
                else "X11: Qt.WindowStaysOnTopHint maps to _NET_WM_STATE_ABOVE",
        "pin_healthy": True,   # trust the Qt flag on Linux; no runtime probe
    })


@app.route("/window-status", methods=["GET"])
def window_status():
    """Diagnostic: report the pet's current macOS window level + collection
    behavior + hidesOnDeactivate for each NSWindow the app owns.

    Used to verify the always-on-top pin from outside the process without
    needing a display.
    """
    import sys
    if sys.platform == "win32":
        return _windows_window_status()
    if sys.platform.startswith("linux"):
        return _linux_window_status()
    if sys.platform != "darwin":
        return jsonify({"platform": sys.platform, "note": "unsupported"})
    try:
        from AppKit import NSApp, NSApplication
        policy = NSApplication.sharedApplication().activationPolicy()
        # 0 = regular (has Dock icon; can't stay above other apps' fullscreen)
        # 1 = accessory (menu bar app; required for fullscreen override)
        # 2 = prohibited
        policy_name = {0: "regular", 1: "accessory", 2: "prohibited"}.get(
            policy, "unknown"
        )
        windows = []
        for w in NSApp().windows():
            try:
                windows.append({
                    "title": str(w.title() or ""),
                    "level": int(w.level()),
                    "collection_behavior": int(w.collectionBehavior()),
                    "hides_on_deactivate": bool(w.hidesOnDeactivate()),
                    "visible": bool(w.isVisible()),
                    "on_active_space": bool(w.isOnActiveSpace()),
                })
            except Exception as exc:
                windows.append({"error": str(exc)})
        target = 1500      # matches PetWindow._MACOS_TARGET_LEVEL
        ok = policy == 1 and all(
            w.get("level", 0) >= target and w.get("hides_on_deactivate") is False
            for w in windows if w.get("visible")
        )
        return jsonify({
            "target_level": target,
            "activation_policy": policy,
            "activation_policy_name": policy_name,
            "pin_healthy": ok,
            "windows": windows,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)})


@app.route("/break", methods=["POST"])
@require_token
def enqueue_break():
    """POST {category, slug} → the pet will open an overlay on its next tick.
    category is optional; if omitted, the pet picks the most-overdue one."""
    global _pending_break
    data = request.json or {}
    _pending_break = {
        "queued": True,
        "category": data.get("category"),
        "slug": data.get("slug"),
    }
    return jsonify({"ok": True, "queued": _pending_break})


@app.route("/break", methods=["GET"])
def peek_break():
    """The pet polls this each tick; if queued=True it opens the overlay and
    clears the queue by POST /break/ack."""
    return jsonify(_pending_break)


@app.route("/break/ack", methods=["POST"])
def ack_break():
    global _pending_break
    _pending_break = {"queued": False, "category": None, "slug": None}
    return jsonify({"ok": True})


@app.route("/shutdown", methods=["POST"])
@require_token
def shutdown():
    """Graceful self-termination — used by `claude-pet start` to replace a
    stale (older-version) pet without pkill. Local-only server, so exposure
    is limited to processes that can already reach localhost:5050."""
    def _die():
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Timer(0.3, _die).start()
    return jsonify({"ok": True, "pid": os.getpid()})


@app.route("/state", methods=["POST"])
@require_token
def update_state():
    global state
    data = request.json or {}
    if "status" not in data:
        return jsonify({"error": "missing 'status'"}), 400
    state["status"] = data["status"]
    state["last_event"] = data.get("event")
    print(f"[server] {state['last_event']!r} -> {state['status']}")
    return jsonify({"success": True})


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify(state)


if __name__ == "__main__":
    app.run(port=5050)
