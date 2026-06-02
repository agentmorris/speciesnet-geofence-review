"""Local Flask app for working through the geofence-review suggestion queue.

Run from this folder, with the speciesnet-geofence-review conda env active:

    python review_app.py

Then open http://127.0.0.1:5000/ in a browser.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from paths import DATA_DIR as DATA_OUT, DECISIONS_FILE
from review_queue import build_queue


# ---------------------------------------------------------------------------
# State

_QUEUE: list[dict[str, Any]] | None = None
_QUEUE_INDEX: dict[str, int] | None = None
_DECISIONS_LOCK = threading.Lock()


def get_queue() -> tuple[list[dict[str, Any]], dict[str, int]]:
    global _QUEUE, _QUEUE_INDEX
    if _QUEUE is None:
        print("Building queue ...")
        q, _ = build_queue()
        _QUEUE = q
        _QUEUE_INDEX = {e["id"]: i for i, e in enumerate(q)}
        print(f"  {len(q)} items")
    return _QUEUE, _QUEUE_INDEX


# ---------------------------------------------------------------------------
# Decision storage

def load_decisions() -> dict[str, Any]:
    if not DECISIONS_FILE.exists():
        return {"decisions": {}}
    try:
        with DECISIONS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # If the file is corrupt, back it up and start fresh.
        backup = DECISIONS_FILE.with_suffix(
            f".corrupt.{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        DECISIONS_FILE.rename(backup)
        return {"decisions": {}}


def save_decisions(data: dict[str, Any]) -> None:
    """Atomic write: write to a temp file in the same dir, then rename."""
    DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".decisions-", suffix=".tmp", dir=str(DECISIONS_FILE.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, DECISIONS_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Flask routes

app = Flask(__name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"))

# Local-only dev quality-of-life: don't cache templates or static files so
# edits to index.html / app.js / style.css show up on a normal browser
# refresh without needing to restart the server or do a hard-refresh.
app.jinja_env.auto_reload = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.route("/")
def index() -> Any:
    return render_template("index.html")


@app.route("/api/queue")
def api_queue() -> Any:
    queue, _ = get_queue()
    return jsonify({
        "count":   len(queue),
        "entries": queue,
    })


@app.route("/api/decisions", methods=["GET"])
def api_decisions_get() -> Any:
    with _DECISIONS_LOCK:
        return jsonify(load_decisions())


@app.route("/api/decision", methods=["POST"])
def api_decision_post() -> Any:
    payload = request.get_json(force=True, silent=False) or {}
    entry_id = payload.get("id")
    if not entry_id:
        return jsonify({"error": "missing id"}), 400

    queue, idx = get_queue()
    if entry_id not in idx:
        return jsonify({"error": "unknown id"}), 404
    entry = queue[idx[entry_id]]

    decision = payload.get("decision")  # may be None to clear
    with _DECISIONS_LOCK:
        data = load_decisions()
        decisions = data.setdefault("decisions", {})
        if decision is None:
            decisions.pop(entry_id, None)
        else:
            decision["updatedAt"]  = dt.datetime.now(dt.timezone.utc).isoformat()
            decision["commonName"] = entry.get("commonName") or ""
            decisions[entry_id] = decision
        save_decisions(data)
    return jsonify({"ok": True, "id": entry_id})


# ---------------------------------------------------------------------------
# Main

def main() -> None:
    get_queue()  # warm up
    print(f"Decisions file: {DECISIONS_FILE}")
    url = "http://127.0.0.1:5000/"
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
