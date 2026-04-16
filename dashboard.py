#!/usr/bin/env python3
"""
Network Health Dashboard — Flask app for Live Ops.
Surfaces site exceptions in real time with a "Track it" action button.
"""

import json
import logging
import subprocess
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv(Path(__file__).parent / ".env")

import exception_tracker
from sheets_writer import SheetsWriter

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

ET = ZoneInfo("America/New_York")
CACHE_TTL = 180  # seconds

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    return yaml.safe_load(cfg_path.read_text())

CFG = _load_config()

def _get_sites_by_pod() -> dict[str, list[str]]:
    return CFG.get("pods", {})

def _get_thresholds() -> dict:
    replan = CFG.get("outreach", {}).get("needs_replan", {})
    return {"yellow": replan.get("yellow", 15), "red": replan.get("red", 35)}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache = {"data": [], "fetched_at": None, "scanning": False}
_cache_lock = threading.Lock()


def _set_timerange():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    subprocess.run(
        ["parcel-cli", "timerange", "--start", str(today), "--end", str(tomorrow)],
        capture_output=True, text=True, timeout=10,
    )


def _get_data(force=False) -> list[dict]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _cache["fetched_at"]:
            age = (now - _cache["fetched_at"]).total_seconds()
            if age < CACHE_TTL:
                return _cache["data"]

        if _cache["scanning"]:
            return _cache["data"]

        _cache["scanning"] = True

    try:
        log.info("Starting exception scan...")
        _set_timerange()
        results = exception_tracker.run(_get_sites_by_pod())
        with _cache_lock:
            _cache["data"] = results
            _cache["fetched_at"] = datetime.now(ET)
            _cache["scanning"] = False
        log.info(f"Scan complete: {len(results)} sites")
        return results
    except Exception as e:
        log.error(f"Scan failed: {e}")
        with _cache_lock:
            _cache["scanning"] = False
        return _cache["data"]


def _cache_info() -> dict:
    with _cache_lock:
        if _cache["fetched_at"]:
            age = (datetime.now(ET) - _cache["fetched_at"]).total_seconds()
            return {
                "last_updated": _cache["fetched_at"].strftime("%-I:%M %p ET"),
                "cache_age_seconds": int(age),
                "scanning": _cache["scanning"],
            }
        return {"last_updated": "Never", "cache_age_seconds": -1, "scanning": _cache["scanning"]}

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    data = _get_data()
    thresholds = _get_thresholds()
    info = _cache_info()
    return render_template(
        "index.html",
        rows=data,
        thresholds=thresholds,
        last_updated=info["last_updated"],
        scanning=info["scanning"],
        refresh_interval=CACHE_TTL * 1000,
    )


@app.route("/api/data")
def api_data():
    data = _get_data()
    info = _cache_info()
    return jsonify({"rows": data, **info})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    data = _get_data(force=True)
    info = _cache_info()
    return jsonify({"rows": data, **info})


@app.route("/api/track", methods=["POST"])
def api_track():
    body = request.get_json()
    if not body or not body.get("site"):
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    now = datetime.now(ET)
    row = {
        "timestamp": now.strftime("%-I:%M %p ET"),
        "site": body["site"],
        "pod": body.get("pod", ""),
        "needs_replan": body.get("needs_replan", 0),
        "missing": body.get("missing", 0),
        "delivery_hold": body.get("delivery_hold", 0),
        "total": body.get("total", 0),
        "analyst": body.get("analyst", ""),
        "action": body.get("action", ""),
        "notes": body.get("notes", ""),
    }

    try:
        writer = SheetsWriter()
        writer.write_tracked_action(row)
        log.info(f"Tracked: {row['site']} by {row['analyst']} — {row['action']}")
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Track write failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
