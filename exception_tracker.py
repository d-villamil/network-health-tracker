"""
Exception tracker — loops active sites, calls parcel-cli, classifies exceptions.
"""

import json
import logging
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent / "state" / "last_run.json"
ET = ZoneInfo("America/New_York")

EXCEPTION_TYPES = {
    "EXCEPTION_TYPE_NEED_REPLAN": "needs_replan",
    "EXCEPTION_TYPE_MISSING": "missing",
    "EXCEPTION_TYPE_DELIVERY_HOLD": "delivery_hold",
}


def _load_prior_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _call_parcel_cli(cmd: list[str]) -> dict | None:
    """Run a parcel-cli command and return parsed JSON, or None on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning(f"parcel-cli error: {' '.join(cmd)}: {result.stderr.strip()}")
            return None
        data = json.loads(result.stdout)
        if not data.get("ok"):
            log.warning(f"parcel-cli returned ok=false: {' '.join(cmd)}")
            return None
        return data["data"]
    except subprocess.TimeoutExpired:
        log.warning(f"parcel-cli timeout: {' '.join(cmd)}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"parcel-cli parse error: {' '.join(cmd)}: {e}")
        return None


def get_exceptions(site: str) -> dict | None:
    """Get exception breakdown for a single site."""
    data = _call_parcel_cli([
        "parcel-cli", "parcel", "exceptions", "-f", site, "--format", "json",
    ])
    if data is None:
        return None

    counts = Counter()
    for row in (data.get("rows") or []):
        exc_type = row.get("exception_type", "UNKNOWN")
        friendly = EXCEPTION_TYPES.get(exc_type, "other")
        counts[friendly] += 1

    return {
        "needs_replan": counts.get("needs_replan", 0),
        "missing": counts.get("missing", 0),
        "delivery_hold": counts.get("delivery_hold", 0),
        "total": sum(counts.values()),
    }


def get_stats(site: str) -> dict | None:
    """Get parcel stats (total_parcel_count, etc.) for a single site."""
    data = _call_parcel_cli([
        "parcel-cli", "parcel", "stats", "-f", site, "--format", "json",
    ])
    if data is None:
        return None

    rows = data.get("rows") or []
    if not rows:
        return None
    return rows[0]


def run(sites_by_pod: dict[str, list[str]]) -> list[dict]:
    """
    Loop all sites, collect exception data + stats.
    Returns a list of dicts ready for sheet writing.
    """
    prior = _load_prior_state()
    now = datetime.now(ET)
    timestamp = now.strftime("%-I:%M %p ET")
    date_str = now.strftime("%Y-%m-%d")

    results = []
    new_state = {}

    for pod, sites in sites_by_pod.items():
        for site in sites:
            log.info(f"Querying {site} ({pod})...")

            exc = get_exceptions(site)
            if exc is None:
                log.warning(f"Skipping {site} — exceptions call failed")
                continue

            stats = get_stats(site)
            total_parcels = stats.get("total_parcel_count", 0) if stats else 0

            prior_total = prior.get(site, {}).get("total", 0)
            delta = exc["total"] - prior_total

            exception_rate = round(exc["total"] / total_parcels, 4) if total_parcels > 0 else 0

            row = {
                "timestamp": timestamp,
                "date": date_str,
                "site": site,
                "pod": pod,
                "needs_replan": exc["needs_replan"],
                "missing": exc["missing"],
                "delivery_hold": exc["delivery_hold"],
                "total": exc["total"],
                "total_parcels": total_parcels,
                "exception_rate": exception_rate,
                "delta": delta,
                "alert_sent": "N",
            }
            results.append(row)

            new_state[site] = {
                "needs_replan": exc["needs_replan"],
                "total": exc["total"],
                "timestamp": now.isoformat(),
            }

            log.info(
                f"  {site}: replan={exc['needs_replan']} missing={exc['missing']} "
                f"hold={exc['delivery_hold']} total={exc['total']} delta={delta:+d}"
            )

    _save_state(new_state)
    log.info(f"Exception scan complete: {len(results)} sites processed")
    return results
