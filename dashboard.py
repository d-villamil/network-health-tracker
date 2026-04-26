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

import cet_tracker
import exception_tracker
import lfr_tracker
import return_bin_tracker
import scorecard_tracker
import shipment_checker
from site_flags_tracker import _load_cached as load_site_flags
import small_batch_tracker
import timeline_tracker
from sheets_writer import SheetsWriter

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

ET = ZoneInfo("America/New_York")
CACHE_TTL = 180  # seconds — parcel-cli data
TRINO_CACHE_TTL = 1800  # 30 minutes — Trino data (return bin)

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
_lfr_cache = {"data": [], "fetched_at": None, "scanning": False}
_cet_cache = {"data": [], "fetched_at": None, "scanning": False}
_return_bin_cache = {"data": [], "fetched_at": None, "scanning": False}
_small_batch_cache = {"data": {}, "fetched_at": None, "scanning": False}
_scorecard_cache = {"data": [], "fetched_at": None, "scanning": False}
_shipment_cache = {"data": [], "fetched_at": None, "scanning": False}
_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Tracked sites (local cache + sheet as source of truth)
# ---------------------------------------------------------------------------

TRACKED_STATE_FILE = Path(__file__).parent / "state" / "tracked_today.json"


def _load_tracked_sites() -> dict:
    """Load today's tracked sites from local cache. Returns {site: {analyst, action, timestamp}}."""
    if TRACKED_STATE_FILE.exists():
        data = json.loads(TRACKED_STATE_FILE.read_text())
        if data.get("date") == str(date.today()):
            return data.get("sites", {})
    return {}


def _save_tracked_site(site: str, analyst: str, action: str, timestamp: str, values: dict = None):
    """Add a tracked site to local cache with snapshot of values at time of tracking."""
    data = {"date": str(date.today()), "sites": _load_tracked_sites()}
    entry = {"analyst": analyst, "action": action, "timestamp": timestamp}
    if values:
        entry["values"] = values
    data["sites"][site] = entry
    TRACKED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRACKED_STATE_FILE.write_text(json.dumps(data, indent=2))


def _sync_tracked_from_sheet():
    """Read today's tracked actions from Google Sheet to sync local cache."""
    try:
        writer = SheetsWriter()
        ss = writer._get_spreadsheet()
        try:
            ws = ss.worksheet(writer._tracked_tab)
        except Exception:
            return {}

        all_rows = ws.get_all_values()
        if len(all_rows) <= 1:
            return {}

        today_str = date.today().strftime("%Y-%m-%d")
        today_et = datetime.now(ET).strftime("%m/%d/%Y")
        tracked = {}

        for row in all_rows[1:]:
            if len(row) < 9:
                continue
            # Match today's entries by checking timestamp contains today's date
            ts = row[0]
            site = row[1]
            analyst = row[7]
            action = row[8]
            if site and (today_str in ts or today_et in ts or date.today().strftime("%-m/%-d") in ts):
                tracked[site] = {"analyst": analyst, "action": action, "timestamp": ts}

        # Save to local cache
        if tracked:
            state = {"date": str(date.today()), "sites": tracked}
            TRACKED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRACKED_STATE_FILE.write_text(json.dumps(state, indent=2))
            log.info(f"Synced {len(tracked)} tracked sites from sheet")

        return tracked
    except Exception as e:
        log.warning(f"Could not sync tracked sites from sheet: {e}")
        return {}


def _get_tracked_sites() -> dict:
    """Get tracked sites — local cache first, sync from sheet if empty."""
    tracked = _load_tracked_sites()
    if not tracked:
        tracked = _sync_tracked_from_sheet()
    return tracked


def _check_and_refresh_auth():
    """Check parcel-cli auth status and trigger login if expired."""
    result = subprocess.run(
        ["parcel-cli", "status"],
        capture_output=True, text=True, timeout=10,
    )
    if "expired" in result.stdout.lower():
        log.warning("parcel-cli auth expired — launching browser login...")
        subprocess.Popen(
            ["parcel-cli", "auth", "login"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for user to complete browser auth
        for i in range(60):
            import time
            time.sleep(2)
            check = subprocess.run(
                ["parcel-cli", "status"],
                capture_output=True, text=True, timeout=10,
            )
            if "expired" not in check.stdout.lower():
                log.info("parcel-cli auth refreshed successfully")
                return True
        log.error("parcel-cli auth refresh timed out — run 'parcel-cli auth login' manually")
        return False
    return True


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
        _check_and_refresh_auth()
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

def _get_lfr_data(force=False) -> list[dict]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _lfr_cache["fetched_at"]:
            age = (now - _lfr_cache["fetched_at"]).total_seconds()
            if age < CACHE_TTL:
                return _lfr_cache["data"]

        if _lfr_cache["scanning"]:
            return _lfr_cache["data"]

        _lfr_cache["scanning"] = True

    try:
        log.info("Starting LFR scan...")
        _set_timerange()
        results = lfr_tracker.run(_get_sites_by_pod())
        with _cache_lock:
            _lfr_cache["data"] = results
            _lfr_cache["fetched_at"] = datetime.now(ET)
            _lfr_cache["scanning"] = False
        log.info(f"LFR scan complete: {len(results)} sites")
        return results
    except Exception as e:
        log.error(f"LFR scan failed: {e}")
        with _cache_lock:
            _lfr_cache["scanning"] = False
        return _lfr_cache["data"]


def _get_cet_data(force=False) -> list[dict]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _cet_cache["fetched_at"]:
            age = (now - _cet_cache["fetched_at"]).total_seconds()
            if age < CACHE_TTL:
                return _cet_cache["data"]

        if _cet_cache["scanning"]:
            return _cet_cache["data"]

        _cet_cache["scanning"] = True

    try:
        log.info("Starting CET scan...")
        _set_timerange()
        results = cet_tracker.run()
        with _cache_lock:
            _cet_cache["data"] = results
            _cet_cache["fetched_at"] = datetime.now(ET)
            _cet_cache["scanning"] = False
        log.info(f"CET scan complete: {len(results)} flagged")
        return results
    except Exception as e:
        log.error(f"CET scan failed: {e}")
        with _cache_lock:
            _cet_cache["scanning"] = False
        return _cet_cache["data"]


def _get_return_bin_data(force=False) -> list[dict]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _return_bin_cache["fetched_at"]:
            age = (now - _return_bin_cache["fetched_at"]).total_seconds()
            if age < TRINO_CACHE_TTL:
                return _return_bin_cache["data"]

        if _return_bin_cache["scanning"]:
            return _return_bin_cache["data"]

        _return_bin_cache["scanning"] = True

    try:
        log.info("Starting return bin scan (Trino)...")
        results = return_bin_tracker.run()
        with _cache_lock:
            _return_bin_cache["data"] = results
            _return_bin_cache["fetched_at"] = datetime.now(ET)
            _return_bin_cache["scanning"] = False
        log.info(f"Return bin scan complete: {len(results)} sites")
        return results
    except Exception as e:
        log.error(f"Return bin scan failed: {e}")
        with _cache_lock:
            _return_bin_cache["scanning"] = False
        return _return_bin_cache["data"]


def _get_small_batch_data(force=False) -> dict[str, int]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _small_batch_cache["fetched_at"]:
            age = (now - _small_batch_cache["fetched_at"]).total_seconds()
            if age < CACHE_TTL:
                return _small_batch_cache["data"]

        if _small_batch_cache["scanning"]:
            return _small_batch_cache["data"]

        _small_batch_cache["scanning"] = True

    try:
        log.info("Starting small batch scan...")
        _set_timerange()
        results = small_batch_tracker.run(_get_sites_by_pod())
        with _cache_lock:
            _small_batch_cache["data"] = results
            _small_batch_cache["fetched_at"] = datetime.now(ET)
            _small_batch_cache["scanning"] = False
        log.info(f"Small batch scan complete")
        return results
    except Exception as e:
        log.error(f"Small batch scan failed: {e}")
        with _cache_lock:
            _small_batch_cache["scanning"] = False
        return _small_batch_cache["data"]


def _get_scorecard_data(force=False) -> list[dict]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _scorecard_cache["fetched_at"]:
            age = (now - _scorecard_cache["fetched_at"]).total_seconds()
            if age < CACHE_TTL:
                return _scorecard_cache["data"]

        if _scorecard_cache["scanning"]:
            return _scorecard_cache["data"]

        _scorecard_cache["scanning"] = True

    try:
        log.info("Starting scorecard scan...")
        _check_and_refresh_auth()
        _set_timerange()
        results = scorecard_tracker.run(_get_sites_by_pod())
        with _cache_lock:
            _scorecard_cache["data"] = results
            _scorecard_cache["fetched_at"] = datetime.now(ET)
            _scorecard_cache["scanning"] = False
        log.info(f"Scorecard scan complete: {len(results)} sites")
        return results
    except Exception as e:
        log.error(f"Scorecard scan failed: {e}")
        with _cache_lock:
            _scorecard_cache["scanning"] = False
        return _scorecard_cache["data"]


def _get_shipment_data(force=False) -> list[dict]:
    with _cache_lock:
        now = datetime.now(ET)
        if not force and _shipment_cache["fetched_at"]:
            age = (now - _shipment_cache["fetched_at"]).total_seconds()
            if age < CACHE_TTL:
                return _shipment_cache["data"]

        if _shipment_cache["scanning"]:
            return _shipment_cache["data"]

        _shipment_cache["scanning"] = True

    try:
        log.info("Starting shipment scan...")
        _set_timerange()
        results = shipment_checker.run()
        with _cache_lock:
            _shipment_cache["data"] = results
            _shipment_cache["fetched_at"] = datetime.now(ET)
            _shipment_cache["scanning"] = False
        log.info(f"Shipment scan complete: {len(results)} flagged")
        return results
    except Exception as e:
        log.error(f"Shipment scan failed: {e}")
        with _cache_lock:
            _shipment_cache["scanning"] = False
        return _shipment_cache["data"]


def _shipment_cache_info() -> dict:
    with _cache_lock:
        if _shipment_cache["fetched_at"]:
            age = (datetime.now(ET) - _shipment_cache["fetched_at"]).total_seconds()
            return {
                "last_updated": _shipment_cache["fetched_at"].strftime("%-I:%M %p ET"),
                "cache_age_seconds": int(age),
            }
        return {"last_updated": "Never", "cache_age_seconds": -1}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _gather_and_update_timeline(data, lfr_data, return_bin_data, small_batch_data):
    """Update timeline with current data and return timeline state."""
    return timeline_tracker.update(data, return_bin_data, lfr_data, small_batch_data)


@app.route("/")
def index():
    data = _get_data()
    lfr_data = _get_lfr_data()
    cet_data = _get_cet_data()
    return_bin_data = _get_return_bin_data()
    small_batch_data = _get_small_batch_data()
    timeline = _gather_and_update_timeline(data, lfr_data, return_bin_data, small_batch_data)
    thresholds = _get_thresholds()
    info = _cache_info()
    tracked = _get_tracked_sites()
    re_engage = CFG.get("re_engage", {"needs_replan": 10})
    return render_template(
        "index.html",
        cet_rows=cet_data,
        timeline=timeline,
        thresholds=thresholds,
        last_updated=info["last_updated"],
        scanning=info["scanning"],
        refresh_interval=CACHE_TTL * 1000,
        tracked_sites=tracked,
        re_engage=re_engage,
    )


@app.route("/api/data")
def api_data():
    data = _get_data()
    lfr_data = _get_lfr_data()
    cet_data = _get_cet_data()
    return_bin_data = _get_return_bin_data()
    small_batch_data = _get_small_batch_data()
    timeline = _gather_and_update_timeline(data, lfr_data, return_bin_data, small_batch_data)
    info = _cache_info()
    tracked = _get_tracked_sites()
    re_engage = CFG.get("re_engage", {"needs_replan": 10})
    return jsonify({"cet_rows": cet_data, "timeline": timeline, "tracked_sites": tracked, "re_engage": re_engage, **info})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    data = _get_data(force=True)
    lfr_data = _get_lfr_data(force=True)
    cet_data = _get_cet_data(force=True)
    return_bin_data = _get_return_bin_data(force=True)
    small_batch_data = _get_small_batch_data(force=True)
    timeline = _gather_and_update_timeline(data, lfr_data, return_bin_data, small_batch_data)
    info = _cache_info()
    return jsonify({"cet_rows": cet_data, "timeline": timeline, **info})


@app.route("/scorecard")
def scorecard_page():
    data = _get_scorecard_data()
    exc_data = _get_data()
    return_bin_data = _get_return_bin_data()
    lfr_data = _get_lfr_data()
    timeline = timeline_tracker.get_timeline()
    info = _cache_info()

    # Build lookup dicts for real-time counts
    exc_by_site = {r["site"]: r for r in exc_data}
    rb_by_site = {r["site"]: r.get("scan_return_bin", 0) for r in return_bin_data}
    lfr_by_site = {r["site"]: r for r in lfr_data}

    # Merge real-time counts into scorecard rows
    for r in data:
        exc = exc_by_site.get(r["site"], {})
        r["needs_replan"] = exc.get("needs_replan", 0)
        r["return_bin"] = rb_by_site.get(r["site"], 0)
        r["lfr_over_45"] = lfr_by_site.get(r["site"], {}).get("lfr_over_45", 0)
        r["dispatch_active"] = lfr_by_site.get(r["site"], {}).get("dispatch_active", False)
        r["plib"] = lfr_by_site.get(r["site"], {}).get("plib", 0)

    site_flags = load_site_flags() or {}
    for r in data:
        r["flags"] = site_flags.get(r["site"], [])

    return render_template("scorecard.html", rows=data, timeline=timeline, last_updated=info["last_updated"])


@app.route("/api/scorecard/refresh", methods=["POST"])
def api_scorecard_refresh():
    data = _get_scorecard_data(force=True)
    exc_data = _get_data()
    return_bin_data = _get_return_bin_data()
    lfr_data = _get_lfr_data()
    timeline = timeline_tracker.get_timeline()
    info = _cache_info()

    exc_by_site = {r["site"]: r for r in exc_data}
    rb_by_site = {r["site"]: r.get("scan_return_bin", 0) for r in return_bin_data}
    lfr_by_site = {r["site"]: r for r in lfr_data}

    for r in data:
        exc = exc_by_site.get(r["site"], {})
        r["needs_replan"] = exc.get("needs_replan", 0)
        r["return_bin"] = rb_by_site.get(r["site"], 0)
        r["lfr_over_45"] = lfr_by_site.get(r["site"], {}).get("lfr_over_45", 0)
        r["dispatch_active"] = lfr_by_site.get(r["site"], {}).get("dispatch_active", False)
        r["plib"] = lfr_by_site.get(r["site"], {}).get("plib", 0)

    site_flags = load_site_flags() or {}
    for r in data:
        r["flags"] = site_flags.get(r["site"], [])

    return jsonify({"rows": data, "timeline": timeline, **info})


@app.route("/shipments")
def shipments_page():
    data = _get_shipment_data()
    info = _shipment_cache_info()
    return render_template("shipments.html", rows=data, last_updated=info["last_updated"])


@app.route("/api/shipments/refresh", methods=["POST"])
def api_shipments_refresh():
    data = _get_shipment_data(force=True)
    info = _shipment_cache_info()
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
        "quantity": body.get("quantity", 0),
        "action": body.get("action", ""),
        "notes": body.get("notes", ""),
    }

    try:
        writer = SheetsWriter()
        writer.write_tracked_action(row)
        _save_tracked_site(row["site"], body.get("analyst", ""), row["action"], row["timestamp"], values={
            "quantity": row["quantity"],
        })
        log.info(f"Tracked: {row['site']} — {row['action']}")
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Track write failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
