"""
Scorecard tracker — builds daily health scorecard per spoke.
Tracks CET met (per inbound truck), sort scan start, and dispatch start.
"""

import json
import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def _get_site_timezones() -> dict[str, str]:
    """Get timezone per facility from parcel-cli."""
    data = _call_parcel_cli_raw(["parcel-cli", "facility", "list", "--format", "json"])
    if data is None:
        return {}
    tzmap = {}
    for r in (data.get("rows") or []):
        code = r.get("facility_code", "")
        tz = (r.get("address") or {}).get("timezone", "")
        if code and tz:
            tzmap[code] = tz
    return tzmap


def _call_parcel_cli_raw(cmd: list[str]) -> dict | None:
    """Same as _call_parcel_cli but returns data dict directly."""
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


def _format_time(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return iso_str


def get_cet_by_spoke() -> dict:
    """
    Get CET status per spoke from shipment list.
    Returns {spoke: {trucks: [{origin, cet, actual, met, minutes_late, carrier}], met_count, total}}
    """
    data = _call_parcel_cli(["parcel-cli", "shipment", "list", "--format", "json"])
    if data is None:
        return {}

    now = datetime.now(ET)
    spoke_data = {}

    for s in (data.get("rows") or []):
        if s.get("status") == "SHIPMENT_STATUS_CANCELED":
            continue
        cet_str = s.get("delivery_appointment_time_at_destination")
        if not cet_str:
            continue

        stops = s.get("shipment_stops") or []
        origin = ""
        dest = ""
        for stop in stops:
            if stop.get("stop_reason") == "STOP_REASON_PICKUP" and not origin:
                origin = stop.get("warehouse_id", "")
            if stop.get("stop_reason") == "STOP_REASON_DROPOFF":
                dest = stop.get("warehouse_id", "")
        if not dest:
            continue

        try:
            cet = datetime.fromisoformat(cet_str.replace("Z", "+00:00")).astimezone(ET)
        except (ValueError, TypeError):
            continue

        actual_str = s.get("actual_dropoff_time_at_destination")
        carrier = (s.get("carrier") or {}).get("carrier_name", "")

        # Extract arrival exception reason
        arrival_reason = ""
        for exc in (s.get("stop_exceptions") or []):
            if exc.get("timestamp_type") == "STOP_EXCEPTION_TIMESTAMP_TYPE_ARRIVAL":
                code = exc.get("reason_code", "")
                label = code.replace("SHIPMENT_EXCEPTION_REASON_CODE_", "").replace("_", " ").title()
                if label and label != "None":
                    arrival_reason = label
                    break

        truck = {
            "shipment_id": s.get("shipment_id", ""),
            "origin": origin,
            "carrier": carrier,
            "cet": _format_time(cet_str),
            "actual": _format_time(actual_str) if actual_str else "",
            "met": False,
            "status": "pending",
            "minutes_late": 0,
            "arrival_reason": arrival_reason,
        }

        if actual_str:
            try:
                actual = datetime.fromisoformat(actual_str.replace("Z", "+00:00")).astimezone(ET)
                diff_min = (actual - cet).total_seconds() / 60
                if diff_min <= 30:
                    truck["met"] = True
                    truck["status"] = "on_time"
                else:
                    truck["status"] = "late"
                    truck["minutes_late"] = int(diff_min)
            except (ValueError, TypeError):
                pass
        else:
            diff_min = (now - cet).total_seconds() / 60
            if diff_min > 30:
                truck["status"] = "pending_late"
                truck["minutes_late"] = int(diff_min)
            else:
                truck["met"] = True
                truck["status"] = "pending_ok"

        if dest not in spoke_data:
            spoke_data[dest] = {"trucks": [], "met_count": 0, "total": 0}
        spoke_data[dest]["trucks"].append(truck)
        spoke_data[dest]["total"] += 1
        if truck["met"]:
            spoke_data[dest]["met_count"] += 1

    return spoke_data


FAILED_SCAN_STATES = {"Missing at spoke", "At wrong facility"}


def get_scan_start(site: str, tz_name: str = "") -> str:
    """Get sort scan start — time of 25th successful scan (assigned to bin) at the spoke."""
    data = _call_parcel_cli(["parcel-cli", "parcel", "list", "-f", site, "--format", "json"])
    if data is None:
        return ""

    site_tz = ZoneInfo(tz_name) if tz_name else ET
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
    if len(local_scans) >= 25:
        return local_scans[24].strftime("%-I:%M %p")
    elif local_scans:
        return local_scans[0].strftime("%-I:%M %p") + f" ({len(local_scans)} scans)"
    return ""


def get_dispatch_times(site: str, tz_name: str = "") -> dict:
    """
    Get dispatch timing from batch list.
    Returns {dispatch_toggle: str, first_runner: str}
    - dispatch_toggle: when first batch entered PREPARING/READY_TO_DISPATCH
    - first_runner: when first runner was assigned today (dispatch confirmed)
    """
    data = _call_parcel_cli(["parcel-cli", "batch", "list", "-f", site, "--format", "json"])
    if data is None:
        return {"dispatch_toggle": "", "first_runner": ""}

    site_tz = ZoneInfo(tz_name) if tz_name else ET
    today_local = datetime.now(site_tz).date()
    rows = data.get("rows") or []
    toggle_times = []
    assigned_times = []

    for r in rows:
        st = r.get("batch_status_type", "")

        # Dispatch toggle: first PREPARING or READY_TO_DISPATCH
        if "PREPARING" in st or "READY_TO_DISPATCH" in st:
            ts = r.get("current_batch_status_timestamp", "")
            if ts:
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(site_tz)
                    if t.date() == today_local:
                        toggle_times.append(t)
                except (ValueError, TypeError):
                    pass

        # First runner assigned today
        ts = r.get("last_assigned_time")
        if ts:
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(site_tz)
                if t.date() == today_local:
                    assigned_times.append(t)
            except (ValueError, TypeError):
                pass

    toggle = min(toggle_times).strftime("%-I:%M %p") if toggle_times else ""
    runner = min(assigned_times).strftime("%-I:%M %p") if assigned_times else ""
    return {"dispatch_toggle": toggle, "first_runner": runner}


def run(sites_by_pod: dict[str, list[str]]) -> list[dict]:
    """Build scorecard data for all spokes using CET from CLI + baselines from Trino."""
    log.info("Fetching CET data from shipments...")
    cet_data = get_cet_by_spoke()

    log.info("Fetching baselines from Trino...")
    from baseline_tracker import run as get_baselines
    baselines = get_baselines()
    baseline_by_site = {r["site"]: r for r in baselines}

    results = []
    for pod, sites in sites_by_pod.items():
        for site in sites:
            cet = cet_data.get(site, {"trucks": [], "met_count": 0, "total": 0})
            bl = baseline_by_site.get(site, {})

            results.append({
                "site": site,
                "pod": pod,
                "cet_trucks": cet["trucks"],
                "cet_met": cet["met_count"],
                "cet_total": cet["total"],
                "scan_start": bl.get("today_scan_start", ""),
                "scan_start_avg": bl.get("avg_scan_start", ""),
                "scan_start_diff": bl.get("scan_diff", 0),
                "dispatch_start": bl.get("today_dispatch_start", ""),
                "dispatch_start_avg": bl.get("avg_dispatch_start", ""),
                "dispatch_diff": bl.get("dispatch_diff", 0),
                "small_batches": bl.get("today_batches_under_15", 0),
                "total_batches": bl.get("today_total_batches", 0),
            })

    log.info(f"Scorecard complete: {len(results)} sites")
    return results
