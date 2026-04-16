"""
Shipment checker — pulls shipment list from parcel-cli, flags late shipments and CET misses.
"""

import json
import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Statuses worth reporting (skip canceled, pending, covered)
ACTIVE_STATUSES = {
    "SHIPMENT_STATUS_IN_TRANSIT_TO_DELIVERY",
    "SHIPMENT_STATUS_DELIVERED",
    "SHIPMENT_STATUS_AT_DELIVERY_STOP",
    "SHIPMENT_STATUS_AT_PICKUP",
}


def _call_parcel_cli(cmd: list[str]) -> dict | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.warning(f"parcel-cli error: {' '.join(cmd)}: {result.stderr.strip()}")
            return None
        data = json.loads(result.stdout)
        if not data.get("ok"):
            log.warning(f"parcel-cli returned ok=false: {' '.join(cmd)}")
            return None
        return data["data"]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
        log.warning(f"parcel-cli error: {' '.join(cmd)}: {e}")
        return None


def _extract_origin_dest(stops: list[dict]) -> tuple[str, str]:
    """Extract first pickup and last dropoff warehouse from shipment stops."""
    origin = ""
    dest = ""
    for stop in stops:
        reason = stop.get("stop_reason", "")
        wh = stop.get("warehouse_id", "")
        if reason == "STOP_REASON_PICKUP" and not origin:
            origin = wh
        if reason == "STOP_REASON_DROPOFF":
            dest = wh  # last dropoff wins
    return origin, dest


def _format_time(iso_str: str | None) -> str:
    """Convert ISO timestamp to readable ET time, or empty string."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%-I:%M %p ET")
    except (ValueError, TypeError):
        return iso_str


def _format_exceptions(stop_exceptions: list[dict]) -> str:
    """Summarize stop exceptions into a readable string."""
    if not stop_exceptions:
        return ""
    parts = []
    for exc in stop_exceptions:
        wh = exc.get("stop_warehouse_code", "?")
        ts_type = exc.get("timestamp_type", "").replace("STOP_EXCEPTION_TIMESTAMP_TYPE_", "").lower()
        reason = exc.get("reason_code", "").replace("SHIPMENT_EXCEPTION_REASON_CODE_", "").lower()
        created_by = exc.get("created_by", "")
        part = f"{wh} {ts_type}"
        if reason and reason != "none":
            part += f" ({reason})"
        if created_by:
            part += f" by {created_by}"
        parts.append(part)
    return "; ".join(parts)


def run() -> list[dict]:
    """
    Pull shipment list and return rows for late or exception shipments.
    """
    data = _call_parcel_cli([
        "parcel-cli", "shipment", "list", "--format", "json",
    ])
    if data is None:
        log.error("Failed to fetch shipment list")
        return []

    rows = data.get("rows") or []
    now = datetime.now(ET)
    timestamp = now.strftime("%-I:%M %p ET")

    results = []
    late_count = 0
    exception_count = 0

    for s in rows:
        status = s.get("status", "")
        if status not in ACTIVE_STATUSES:
            continue

        is_late = s.get("is_late", False)
        stop_exceptions = s.get("stop_exceptions") or []

        if not is_late and not stop_exceptions:
            continue

        origin, dest = _extract_origin_dest(s.get("shipment_stops") or [])
        carrier = (s.get("carrier") or {}).get("carrier_name", "")
        cet = _format_time(s.get("delivery_appointment_time_at_destination"))
        actual_dropoff = _format_time(s.get("actual_dropoff_time_at_destination"))
        exc_summary = _format_exceptions(stop_exceptions)

        row = {
            "timestamp": timestamp,
            "shipment_id": s.get("shipment_id", ""),
            "carrier": carrier,
            "origin": origin,
            "destination": dest,
            "cet": cet,
            "actual_dropoff": actual_dropoff,
            "late": "Y" if is_late else "N",
            "stop_exceptions": exc_summary,
        }
        results.append(row)

        if is_late:
            late_count += 1
        if stop_exceptions:
            exception_count += 1

    log.info(f"Shipment check: {len(rows)} total, {len(results)} flagged ({late_count} late, {exception_count} with exceptions)")
    return results
