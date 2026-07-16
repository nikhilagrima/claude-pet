from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

state = {"status": "idle", "last_event": None}


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
