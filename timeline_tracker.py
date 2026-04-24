"""
Timeline tracker — logs threshold crossing events throughout the day.
Each refresh checks if any metric crossed a new threshold and records it.
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
STATE_FILE = Path(__file__).parent / "state" / "timeline_today.json"

# Thresholds that trigger a timeline event
THRESHOLDS = {
    "needs_replan": 15,
    "return_bin": 5,
    "lfr_over_45": 1,
    "small_batches": 1,
}

METRIC_LABELS = {
    "needs_replan": "Needs Replan",
    "return_bin": "Return Bin",
    "lfr_over_45": "LFR > 45 min",
    "small_batches": "Small Batches",
}


def _load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if data.get("date") == str(date.today()):
            return data
    return {"date": str(date.today()), "sites": {}}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def update(exceptions: list[dict], return_bins: list[dict],
           lfr: list[dict], small_batches: dict[str, int]) -> dict:
    """
    Check current values against thresholds, log new events.
    Returns the full timeline state: {sites: {site: {events: [...], crossed: {...}}}}
    """
    state = _load_state()
    now = datetime.now(ET).strftime("%-I:%M %p ET")

    # Build lookup dicts
    exc_by_site = {r["site"]: r for r in exceptions}
    rb_by_site = {r["site"]: r.get("scan_return_bin", 0) for r in return_bins}
    lfr_by_site = {r["site"]: r for r in lfr}

    # Collect all sites
    all_sites = set()
    for r in exceptions:
        all_sites.add(r["site"])
    for r in return_bins:
        all_sites.add(r["site"])
    for r in lfr:
        all_sites.add(r["site"])
    for site in small_batches:
        all_sites.add(site)

    for site in all_sites:
        current = {
            "needs_replan": exc_by_site.get(site, {}).get("needs_replan", 0),
            "return_bin": rb_by_site.get(site, 0),
            "lfr_over_45": lfr_by_site.get(site, {}).get("lfr_over_45", 0),
            "small_batches": small_batches.get(site, 0),
        }

        site_state = state["sites"].setdefault(site, {"events": [], "crossed": {}})
        crossed = site_state["crossed"]

        for metric, threshold in THRESHOLDS.items():
            value = current[metric]
            if value < threshold:
                continue

            prev_value = crossed.get(metric, 0)
            # Log if first crossing or value increased significantly (20%+)
            if prev_value < threshold or (prev_value > 0 and value >= prev_value * 1.2):
                label = f"{METRIC_LABELS[metric]} hit {value}"
                site_state["events"].append({
                    "time": now,
                    "metric": metric,
                    "value": value,
                    "label": label,
                })
                crossed[metric] = value
                log.info(f"Timeline event: {site} — {label}")

    _save_state(state)

    # Return only sites with events
    active = {}
    for site, site_state in state["sites"].items():
        if site_state["events"]:
            active[site] = site_state

    return active


def get_timeline() -> dict:
    """Get current timeline state (read-only, no update)."""
    state = _load_state()
    active = {}
    for site, site_state in state["sites"].items():
        if site_state["events"]:
            active[site] = site_state
    return active
