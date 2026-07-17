import os
import signal
import threading

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


@app.route("/version", methods=["GET"])
def get_version():
    """Lets `claude-pet start` detect a stale running pet after an upgrade."""
    return jsonify({"version": __version__, "pid": os.getpid()})


@app.route("/break", methods=["POST"])
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
def shutdown():
    """Graceful self-termination — used by `claude-pet start` to replace a
    stale (older-version) pet without pkill. Local-only server, so exposure
    is limited to processes that can already reach localhost:5050."""
    def _die():
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Timer(0.3, _die).start()
    return jsonify({"ok": True, "pid": os.getpid()})


@app.route("/state", methods=["POST"])
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
