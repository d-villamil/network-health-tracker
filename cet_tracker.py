"""
CET (Critical Entry Time) tracker — flags shipments late to spoke.
Late = actual dropoff > delivery appointment by 30+ min, or CET passed with no arrival.
"""

import json
import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LATE_THRESHOLD_MIN = 30

REASON_LABELS = {
    "SHIPMENT_EXCEPTION_REASON_CODE_NONE": "",
    "SHIPMENT_EXCEPTION_REASON_CODE_HELD_AT_SHIPPER": "Held at Shipper",
    "SHIPMENT_EXCEPTION_REASON_CODE_MERCHANT_NOT_READY": "Merchant Not Ready",
    "SHIPMENT_EXCEPTION_REASON_CODE_DASHLINK_TENDER_ERROR": "Tender Error",
    "SHIPMENT_EXCEPTION_REASON_CODE_TRAFFIC": "Traffic",
    "SHIPMENT_EXCEPTION_REASON_CODE_MECHANICAL_FAILURE": "Mechanical Failure",
    "SHIPMENT_EXCEPTION_REASON_CODE_DRIVER_ERROR_HOS": "Driver HOS",
    "SHIPMENT_EXCEPTION_REASON_CODE_CARRIER_DISPATCH_ERROR": "Carrier Dispatch Error",
    "SHIPMENT_EXCEPTION_REASON_CODE_TMS_EDI_DATA_ERROR": "TMS/EDI Data Error",
}


def _extract_exception_reasons(stop_exceptions: list[dict]) -> str:
    if not stop_exceptions:
        return ""
    # Group by reason, collect which timestamp types apply
    reason_types: dict[str, set[str]] = {}
    for exc in stop_exceptions:
        code = exc.get("reason_code", "")
        label = REASON_LABELS.get(code, code.replace("SHIPMENT_EXCEPTION_REASON_CODE_", "").replace("_", " ").title())
        if not label:
            continue
        ts_type = exc.get("timestamp_type", "").replace("STOP_EXCEPTION_TIMESTAMP_TYPE_", "").lower()
        reason_types.setdefault(label, set()).add(ts_type)

    parts = []
    for reason, types in sorted(reason_types.items()):
        type_str = ", ".join(sorted(types))
        parts.append(f"{reason} ({type_str})")
    return "; ".join(parts) if parts else ""


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
        return dt.astimezone(ET).strftime("%-I:%M %p ET")
    except (ValueError, TypeError):
        return iso_str


def _extract_dest(stops: list[dict]) -> str:
    for stop in stops:
        if stop.get("stop_reason") == "STOP_REASON_DROPOFF":
            return stop.get("warehouse_id", "")
    return ""


def _extract_origin(stops: list[dict]) -> str:
    for stop in stops:
        if stop.get("stop_reason") == "STOP_REASON_PICKUP":
            return stop.get("warehouse_id", "")
    return ""


def run() -> list[dict]:
    """
    Pull shipment list and flag CET-late shipments.
    Returns list of dicts for shipments that are 30+ min late or pending past CET.
    """
    data = _call_parcel_cli([
        "parcel-cli", "shipment", "list", "--format", "json",
    ])
    if data is None:
        log.error("Failed to fetch shipment list for CET check")
        return []

    rows = data.get("rows") or []
    now = datetime.now(ET)
    results = []

    for s in rows:
        status = s.get("status", "")
        if status == "SHIPMENT_STATUS_CANCELED":
            continue

        cet_str = s.get("delivery_appointment_time_at_destination")
        if not cet_str:
            continue

        try:
            cet = datetime.fromisoformat(cet_str.replace("Z", "+00:00")).astimezone(ET)
        except (ValueError, TypeError):
            continue

        actual_str = s.get("actual_dropoff_time_at_destination")
        carrier = (s.get("carrier") or {}).get("carrier_name", "")
        stops = s.get("shipment_stops") or []
        origin = _extract_origin(stops)
        dest = _extract_dest(stops)

        exc_reason = _extract_exception_reasons(s.get("stop_exceptions") or [])

        if actual_str:
            # Arrived — check if 30+ min late
            try:
                actual = datetime.fromisoformat(actual_str.replace("Z", "+00:00")).astimezone(ET)
            except (ValueError, TypeError):
                continue
            diff_min = (actual - cet).total_seconds() / 60
            if diff_min < LATE_THRESHOLD_MIN:
                continue

            results.append({
                "shipment_id": s.get("shipment_id", ""),
                "carrier": carrier,
                "origin": origin,
                "destination": dest,
                "cet": _format_time(cet_str),
                "actual_arrival": _format_time(actual_str),
                "status": "Arrived Late",
                "minutes_late": int(diff_min),
                "exception_reason": exc_reason,
            })
        else:
            # No arrival yet — check if CET has passed
            diff_min = (now - cet).total_seconds() / 60
            if diff_min < LATE_THRESHOLD_MIN:
                continue

            results.append({
                "shipment_id": s.get("shipment_id", ""),
                "carrier": carrier,
                "origin": origin,
                "destination": dest,
                "cet": _format_time(cet_str),
                "actual_arrival": "",
                "status": "Pending",
                "minutes_late": int(diff_min),
                "exception_reason": exc_reason,
            })

    results.sort(key=lambda r: r["minutes_late"], reverse=True)
    arrived_late = sum(1 for r in results if r["status"] == "Arrived Late")
    pending = sum(1 for r in results if r["status"] == "Pending")
    log.info(f"CET check: {len(results)} flagged ({arrived_late} arrived late, {pending} pending past CET)")
    return results
