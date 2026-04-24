"""
Small batch tracker — counts active batches with < 15 parcels per site.
"""

import json
import logging
import subprocess

log = logging.getLogger(__name__)

SMALL_THRESHOLD = 15


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


def get_small_batches(site: str) -> int:
    """Count active batches with < 15 parcels at a site."""
    data = _call_parcel_cli([
        "parcel-cli", "batch", "list", "-f", site, "--format", "json",
    ])
    if data is None:
        return 0

    count = 0
    for row in (data.get("rows") or []):
        if row.get("batch_status_type") == "BATCH_STATUS_TYPE_RUNNER_HANDOFF_COMPLETE":
            continue
        parcels = row.get("partial_parcels_count") or 0
        if 0 < parcels < SMALL_THRESHOLD:
            count += 1
    return count


def run(sites_by_pod: dict[str, list[str]]) -> dict[str, int]:
    """Return {site: small_batch_count} for all sites."""
    results = {}
    for pod, sites in sites_by_pod.items():
        for site in sites:
            results[site] = get_small_batches(site)

    total = sum(1 for v in results.values() if v > 0)
    log.info(f"Small batch scan complete: {total} sites with small batches")
    return results
