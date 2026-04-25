"""
Baseline tracker — queries Trino for 30-day averages and today's scan/dispatch times.
Replaces the slow CLI-based scan_history.py with a single Trino query.
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import trino
import trino.auth

log = logging.getLogger(__name__)

SQL_FILE = Path(__file__).parent / "sql" / "scorecard_baselines.sql"
ET = ZoneInfo("America/New_York")

TRINO_HOST = "trino.doordash.team"
TRINO_PORT = 443


def _connect():
    user = os.environ.get("TRINO_USER", "")
    token = os.environ.get("TRINO_TOKEN")
    if token:
        auth = trino.auth.JWTAuthentication(token)
    else:
        auth = trino.auth.OAuth2Authentication()

    return trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=user,
        catalog="datalake",
        http_scheme="https",
        auth=auth,
        verify=True,
    )


def _format_hhmm(time_str: str | None) -> str:
    """Convert HH:mm:ss to h:MM AM/PM format."""
    if not time_str:
        return ""
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        period = "AM" if h < 12 else "PM"
        display_h = h if h <= 12 else h - 12
        if display_h == 0:
            display_h = 12
        return f"{display_h}:{m:02d} {period}"
    except (ValueError, IndexError):
        return time_str


def _format_timestamp(ts) -> str:
    """Convert a timestamp to h:MM AM/PM format."""
    if ts is None:
        return ""
    try:
        if isinstance(ts, datetime):
            return ts.strftime("%-I:%M %p")
        return str(ts)
    except (ValueError, TypeError):
        return str(ts) if ts else ""


def _time_to_minutes(time_str: str | None) -> float | None:
    """Convert HH:mm:ss to minutes since midnight."""
    if not time_str:
        return None
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def _ts_to_minutes(ts) -> float | None:
    """Convert timestamp to minutes since midnight."""
    if ts is None:
        return None
    try:
        if isinstance(ts, datetime):
            return ts.hour * 60 + ts.minute
        return None
    except (ValueError, TypeError):
        return None


def run() -> list[dict]:
    """Query Trino for baseline averages and today's values. Returns list of dicts per site."""
    sql = SQL_FILE.read_text()

    for attempt in range(1, 4):
        try:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            log.info(f"Baseline query OK: {len(rows)} sites, attempt {attempt}")

            results = []
            for r in rows:
                # Avg scan start
                avg_scan = _format_timestamp(r.get("avg_start_clock_local_ts"))
                avg_scan_min = _ts_to_minutes(r.get("avg_start_clock_local_ts"))

                # Today scan start
                today_scan = _format_timestamp(r.get("today_start_clock_local_ts"))
                today_scan_min = _ts_to_minutes(r.get("today_start_clock_local_ts"))

                # Scan diff
                scan_diff = 0
                if today_scan_min is not None and avg_scan_min is not None:
                    scan_diff = int(today_scan_min - avg_scan_min)

                # Avg dispatch start
                avg_dispatch = _format_hhmm(r.get("avg_dispatch_start_time_local"))
                avg_dispatch_min = _time_to_minutes(r.get("avg_dispatch_start_time_local"))

                # Today dispatch start
                today_dispatch = _format_hhmm(r.get("today_dispatch_start_time_local"))
                today_dispatch_min = _time_to_minutes(r.get("today_dispatch_start_time_local"))

                # Dispatch diff
                dispatch_diff = 0
                if today_dispatch_min is not None and avg_dispatch_min is not None:
                    dispatch_diff = int(today_dispatch_min - avg_dispatch_min)

                results.append({
                    "site": r.get("facility_code", ""),
                    "pod": r.get("pod", ""),
                    "timezone": r.get("timezone", ""),
                    "avg_scan_start": avg_scan,
                    "today_scan_start": today_scan,
                    "scan_diff": scan_diff,
                    "avg_dispatch_start": avg_dispatch,
                    "today_dispatch_start": today_dispatch,
                    "dispatch_diff": dispatch_diff,
                    "today_batches_under_15": r.get("today_batches_under_15") or 0,
                    "today_total_batches": r.get("today_total_batches") or 0,
                })

            return results
        except Exception as e:
            log.warning(f"Baseline query attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(5 * attempt)

    log.error("Baseline query failed after 3 attempts")
    return []
