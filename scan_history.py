"""
Scan history — computes 5-day average sort scan start per site.
Cached daily in state/scan_averages.json.
"""

import json
import logging
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent / "state" / "scan_averages.json"
LOOKBACK_DAYS = 5
SCAN_THRESHOLD = 25
FAILED_SCAN_STATES = {"Missing at spoke", "At wrong facility"}


def _call_parcel_cli(cmd: list[str]) -> dict | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not data.get("ok"):
            return None
        return data["data"]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
        return None


def _set_timerange(start: date, end: date):
    subprocess.run(
        ["parcel-cli", "timerange", "--start", str(start), "--end", str(end)],
        capture_output=True, text=True, timeout=10,
    )


def _get_scan_start_minutes(site: str, tz_name: str) -> float | None:
    """Get the 25th successful scan time as minutes since midnight in site's local tz."""
    data = _call_parcel_cli(["parcel-cli", "parcel", "list", "-f", site, "--format", "json"])
    if data is None:
        return None

    site_tz = ZoneInfo(tz_name)
    local_scans = []
    for r in (data.get("rows") or []):
        if r.get("last_scanned_facility_code") != site:
            continue
        state = ""
        for s in (r.get("parcel_states") or []):
            state = s.get("parcel_state", "")
            break
        if state in FAILED_SCAN_STATES:
            continue

        ts = r.get("last_scanned_at") or r.get("first_scanned_at")
        if ts:
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(site_tz)
                local_scans.append(t)
            except (ValueError, TypeError):
                continue

    local_scans.sort()
    if len(local_scans) >= SCAN_THRESHOLD:
        t = local_scans[SCAN_THRESHOLD - 1]
        return t.hour * 60 + t.minute
    return None


def load_cached() -> dict | None:
    """Load today's cached averages. Returns None if cache is stale or missing."""
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if data.get("date") == str(date.today()):
            return data.get("sites", {})
    return None


def compute_averages(sites_by_pod: dict[str, list[str]], tz_map: dict[str, str]) -> dict:
    """
    Compute 5-day average scan start for all sites.
    Returns {site: {avg_minutes: float, avg_time: str, days: int}}
    """
    today = date.today()
    all_sites = []
    for pod, sites in sites_by_pod.items():
        for site in sites:
            all_sites.append(site)

    site_history: dict[str, list[float]] = {site: [] for site in all_sites}

    for day_offset in range(1, LOOKBACK_DAYS + 1):
        day = today - timedelta(days=day_offset)
        next_day = day + timedelta(days=1)

        # Skip weekends (Sat=5, Sun=6)
        if day.weekday() in (5, 6):
            continue

        log.info(f"Scan history: fetching {day}...")
        _set_timerange(day, next_day)

        for site in all_sites:
            tz_name = tz_map.get(site, "America/New_York")
            minutes = _get_scan_start_minutes(site, tz_name)
            if minutes is not None:
                site_history[site].append(minutes)

    # Reset timerange back to today
    _set_timerange(today, today + timedelta(days=1))

    # Compute averages
    results = {}
    for site, values in site_history.items():
        if not values:
            continue
        avg = sum(values) / len(values)
        hrs = int(avg // 60)
        mins = int(avg % 60)
        period = "AM" if hrs < 12 else "PM"
        display_hr = hrs if hrs <= 12 else hrs - 12
        if display_hr == 0:
            display_hr = 12
        results[site] = {
            "avg_minutes": round(avg, 1),
            "avg_time": f"{display_hr}:{mins:02d} {period}",
            "days": len(values),
        }

    # Save cache
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"date": str(today), "sites": results}, indent=2))
    log.info(f"Scan averages computed and cached: {len(results)} sites with data")

    return results


def get_averages(sites_by_pod: dict[str, list[str]], tz_map: dict[str, str]) -> dict:
    """Get averages — from cache if available, otherwise compute."""
    cached = load_cached()
    if cached is not None:
        log.info(f"Scan averages loaded from cache: {len(cached)} sites")
        return cached
    log.info("No cached scan averages for today — computing (this takes ~13 min)...")
    return compute_averages(sites_by_pod, tz_map)
