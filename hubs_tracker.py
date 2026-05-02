"""
Hubs tracker — pulls outbound kiosk data for each hub via parcel-cli.
Feeds the /hubs dashboard tab and the static gh-pages hubs page.
"""

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

HUBS = ["ORD-7", "ATL-13", "GCO-1", "DTX-1", "EWR-2", "LAX-12"]


def _format_time(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return ""


def _parse_iso(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


_EQUIPMENT_LABELS = {
    "EQUIPMENT_TYPE_53_VAN": "53' Van",
    "EQUIPMENT_TYPE_53_VAN_WITH_DROP_TRAILER": "53' Van (Drop)",
    "EQUIPMENT_TYPE_26_BOX_TRUCK": "26' Box",
    "EQUIPMENT_TYPE_26_BOX_TRUCK_WITH_LIFT_GATE": "26' Box (Liftgate)",
    "EQUIPMENT_TYPE_UNSPECIFIED": "—",
}


def _equipment_label(eq: str | None) -> str:
    if not eq:
        return "—"
    return _EQUIPMENT_LABELS.get(eq, eq.replace("EQUIPMENT_TYPE_", "").replace("_", " ").title())


def _status_label(status: str | None) -> str:
    if not status:
        return "—"
    return status.replace("CONTAINER_STATUS_", "").replace("_", " ").title()


def _inbound_status_label(status: str | None) -> str:
    if not status:
        return "—"
    return status.replace("INBOUND_TRUCK_SORTATION_STATUS_", "").replace("_", " ").title()


def _fetch_hub_outbound(hub: str) -> list[dict]:
    """Call parcel-cli for one hub and return normalized rows."""
    result = subprocess.run(
        ["parcel-cli", "container", "outbound-kiosk", "-f", hub, "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.warning(f"outbound-kiosk failed for {hub}: {result.stderr.strip()}")
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log.warning(f"outbound-kiosk JSON error for {hub}: {e}")
        return []
    if not data.get("ok"):
        return []

    rows = data.get("data", {}).get("rows") or []
    now = datetime.now(ET)
    out = []
    for r in rows:
        sd = r.get("shipment_details") or {}
        scheduled = _parse_iso(r.get("scheduled_departure_time"))
        actual = _parse_iso(r.get("actual_departure_time"))
        status = r.get("status") or ""

        # Late: actual departure later than scheduled, or not departed but scheduled has passed
        is_late = False
        if actual and scheduled and actual > scheduled:
            is_late = True
        elif not actual and scheduled and now > scheduled and status != "CONTAINER_STATUS_DEPARTED":
            is_late = True

        stowed = r.get("stowed_child_container_count") or 0
        eligible = r.get("eligible_child_containers_to_load_count") or 0
        is_empty = (stowed == 0) and status != "CONTAINER_STATUS_DEPARTED"

        loc = sd.get("latest_location") or {}

        out.append({
            "destination": r.get("destination_location") or "",
            "status": _status_label(status),
            "status_raw": status,
            "dock": r.get("last_dock_door_name") or "",
            "scheduled_departure": _format_time(r.get("scheduled_departure_time")),
            "actual_departure": _format_time(r.get("actual_departure_time")),
            "stowed": stowed,
            "eligible": eligible,
            "equipment": _equipment_label(sd.get("equipment_type")),
            "shipment_id": sd.get("shipment_id") or "",
            "eta": _format_time(sd.get("eta")),
            "weight_lbs": round(r.get("total_weight_in_lbs") or 0, 1),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "is_late": is_late,
            "is_empty": is_empty,
        })

    # Sort: not departed first (sorted by scheduled departure), then departed (most recent first)
    def sort_key(row):
        is_departed = row["status_raw"] == "CONTAINER_STATUS_DEPARTED"
        return (is_departed, row["scheduled_departure"] or "zzz")

    out.sort(key=sort_key)
    return out


def _fetch_set_origins(hub: str, set_id: str) -> list[tuple[str, int]]:
    """Return [(origin_warehouse, expected_parcel_count), ...] for a sortation set, sorted by count desc."""
    result = subprocess.run(
        ["parcel-cli", "container", "inbound-sources", "-f", hub, set_id, "--format", "json"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    rows = data.get("data", {}).get("rows") or []
    counts = {}
    for r in rows:
        origin = r.get("origin_warehouse_name") or ""
        if not origin:
            continue
        cnt = (r.get("parcel_induction_stats") or {}).get("expected_count") or 0
        counts[origin] = counts.get(origin, 0) + cnt
    return sorted(counts.items(), key=lambda x: -x[1])


def _format_origins(origins: list[tuple[str, int]], top_n: int = 3) -> str:
    """Format origins as 'A (120), B (95), +2 more'."""
    if not origins:
        return ""
    top = origins[:top_n]
    parts = [f"{name} ({cnt})" for name, cnt in top]
    if len(origins) > top_n:
        parts.append(f"+{len(origins) - top_n} more")
    return ", ".join(parts)


def _fetch_hub_inbound(hub: str) -> list[dict]:
    """Call parcel-cli sortation-stats for one hub and enrich with origin lookups."""
    result = subprocess.run(
        ["parcel-cli", "facility", "sortation-stats", "-f", hub, "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        log.warning(f"sortation-stats failed for {hub}: {result.stderr.strip()}")
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log.warning(f"sortation-stats JSON error for {hub}: {e}")
        return []
    if not data.get("ok"):
        return []

    rows = data.get("data", {}).get("rows") or []

    # Parallel-fetch origins for each set
    set_ids = [r.get("sortation_set_id") for r in rows]
    origins_by_id = {}
    if set_ids:
        with ThreadPoolExecutor(max_workers=12) as ex:
            for sid, origins in zip(set_ids, ex.map(lambda s: _fetch_set_origins(hub, s), set_ids)):
                origins_by_id[sid] = origins

    out = []
    for r in rows:
        stats = r.get("parcel_induction_stats") or {}
        expected = stats.get("expected_count") or 0
        inducted = stats.get("inducted_count") or 0
        progress = round((inducted / expected) * 100) if expected else 0
        status = r.get("scan_status") or ""
        sid = r.get("sortation_set_id") or ""
        origins = origins_by_id.get(sid, [])
        out.append({
            "set_number": r.get("sortation_set_number") or 0,
            "set_id": sid,
            "status": _inbound_status_label(status),
            "status_raw": status,
            "expected": expected,
            "inducted": inducted,
            "progress": progress,
            "zone_code": r.get("zone_code") or "",
            "start_time": _format_time(r.get("start_time")),
            "origins": _format_origins(origins),
            "origin_count": len(origins),
        })
    out.sort(key=lambda r: r["set_number"])
    return out


def run() -> dict:
    """Fetch outbound + inbound kiosk data for all hubs in parallel.
    Returns {hub: {outbound: [rows], inbound: [rows]}}.
    """
    def _scan(hub):
        return hub, _fetch_hub_outbound(hub), _fetch_hub_inbound(hub)

    results = {}
    total_out = 0
    total_in = 0
    with ThreadPoolExecutor(max_workers=len(HUBS)) as ex:
        for hub, outbound, inbound in ex.map(_scan, HUBS):
            results[hub] = {"outbound": outbound, "inbound": inbound}
            total_out += len(outbound)
            total_in += len(inbound)
    log.info(f"Hubs scan: {total_out} outbound trucks, {total_in} inbound sort sets across {len(HUBS)} hubs")
    return results
