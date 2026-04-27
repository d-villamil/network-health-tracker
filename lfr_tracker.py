"""
LFR (Looking For Runners) tracker — counts batches stuck in LFR state per site.
"""

import json
import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LFR_STATUSES = {
    "BATCH_STATUS_TYPE_LOOKING_FOR_RUNNERS",
    "BATCH_STATUS_TYPE_PREPARING",
    "BATCH_STATUS_TYPE_READY_TO_DISPATCH",
}


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


def get_lfr_batches(site: str) -> dict:
    """
    Get LFR batch info for a site.
    Returns: {
        "total_lfr": int,          # all LFR batches
        "lfr_over_45": int,        # LFR batches waiting > 45 min
        "first_lfr_time": str,     # earliest LFR timestamp (ET), or ""
        "max_wait_min": int,       # longest wait in minutes
    }
    """
    data = _call_parcel_cli([
        "parcel-cli", "batch", "list", "-f", site, "--format", "json",
    ])
    if data is None:
        return {"total_lfr": 0, "lfr_over_45": 0, "first_lfr_time": "", "max_wait_min": 0, "dispatch_active": False}

    DISPATCH_ACTIVE_STATUSES = {
        "BATCH_STATUS_TYPE_LOOKING_FOR_RUNNERS",
        "BATCH_STATUS_TYPE_RUNNER_ASSIGNED",
        "BATCH_STATUS_TYPE_RUNNER_APPROACHING",
        "BATCH_STATUS_TYPE_RUNNER_SCANNING_BATCH",
    }

    now = datetime.now(ET)
    lfr_batches = []
    PLIB_STATUSES = {
        "BATCH_STATUS_TYPE_PREPARING",
        "BATCH_STATUS_TYPE_READY_TO_DISPATCH",
    }

    has_active_runners = any(
        row.get("batch_status_type", "") in DISPATCH_ACTIVE_STATUSES
        for row in (data.get("rows") or [])
    )

    # Check dispatch toggle from dispatch-stats
    dispatch_stats = _call_parcel_cli([
        "parcel-cli", "batch", "dispatch-stats", "-f", site, "--format", "json",
    ])
    dispatch_toggle = False
    if dispatch_stats:
        for row in (dispatch_stats.get("rows") or []):
            if row.get("automatic_dispatch_status"):
                dispatch_toggle = True
                break

    dispatch_active = dispatch_toggle or has_active_runners

    plib = sum(
        (row.get("partial_parcels_count") or 0)
        for row in (data.get("rows") or [])
        if row.get("batch_status_type", "") in PLIB_STATUSES
    )

    for row in (data.get("rows") or []):
        if row.get("batch_status_type") not in LFR_STATUSES:
            continue

        ts = row.get("current_batch_status_timestamp", "")
        if not ts:
            continue

        try:
            batch_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET)
            wait_min = (now - batch_time).total_seconds() / 60
            lfr_batches.append({"time": batch_time, "wait_min": wait_min})
        except (ValueError, TypeError):
            continue

    if not lfr_batches:
        return {"total_lfr": 0, "lfr_over_45": 0, "first_lfr_time": "", "max_wait_min": 0, "dispatch_active": dispatch_active, "dispatch_toggle": dispatch_toggle, "has_active_runners": has_active_runners, "plib": plib}

    over_45 = [b for b in lfr_batches if b["wait_min"] > 45]
    earliest = min(lfr_batches, key=lambda b: b["time"])
    longest = max(lfr_batches, key=lambda b: b["wait_min"])

    return {
        "total_lfr": len(lfr_batches),
        "lfr_over_45": len(over_45),
        "first_lfr_time": earliest["time"].strftime("%-I:%M %p ET"),
        "max_wait_min": int(longest["wait_min"]),
        "dispatch_active": dispatch_active,
        "dispatch_toggle": dispatch_toggle,
        "has_active_runners": has_active_runners,
        "plib": plib,
    }


def run(sites_by_pod: dict[str, list[str]]) -> list[dict]:
    """Loop all sites, collect LFR data. Returns list of dicts."""
    results = []

    for pod, sites in sites_by_pod.items():
        for site in sites:
            lfr = get_lfr_batches(site)

            results.append({
                "site": site,
                "pod": pod,
                "total_lfr": lfr["total_lfr"],
                "lfr_over_45": lfr["lfr_over_45"],
                "first_lfr_time": lfr["first_lfr_time"],
                "max_wait_min": lfr["max_wait_min"],
                "dispatch_active": lfr["dispatch_active"],
                "dispatch_toggle": lfr["dispatch_toggle"],
                "has_active_runners": lfr["has_active_runners"],
                "plib": lfr["plib"],
            })

    sites_with_lfr = sum(1 for r in results if r["total_lfr"] > 0)
    log.info(f"LFR scan complete: {len(results)} sites, {sites_with_lfr} with active LFR")
    return results
