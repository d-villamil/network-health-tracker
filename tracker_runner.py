#!/usr/bin/env python3
"""
Tracker runner — pulls data from all sources and writes to the Network Health Tracker sheet.
Updates fixed site rows in-place every run.

Usage:
  python tracker_runner.py --once --dry-run
  python tracker_runner.py --once
"""

import argparse
import logging
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger("tracker")


def setup_logging():
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "tracker.log"),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    return yaml.safe_load(cfg_path.read_text())


def _check_and_refresh_auth():
    result = subprocess.run(
        ["parcel-cli", "status"],
        capture_output=True, text=True, timeout=10,
    )
    if "expired" in result.stdout.lower():
        log.warning("parcel-cli auth expired — launching browser login...")
        subprocess.Popen(
            ["parcel-cli", "auth", "login"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        import time
        for i in range(60):
            time.sleep(2)
            check = subprocess.run(
                ["parcel-cli", "status"],
                capture_output=True, text=True, timeout=10,
            )
            if "expired" not in check.stdout.lower():
                log.info("parcel-cli auth refreshed successfully")
                return True
        log.error("parcel-cli auth refresh timed out")
        return False
    return True


def _set_timerange():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    subprocess.run(
        ["parcel-cli", "timerange", "--start", str(today), "--end", str(tomorrow)],
        capture_output=True, text=True, timeout=10,
    )


def main():
    parser = argparse.ArgumentParser(description="Network Health Tracker runner")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Log only, no writes")
    args = parser.parse_args()

    setup_logging()

    cfg = load_config()
    dry_run = args.dry_run
    sites_by_pod = cfg.get("pods", {})
    total_sites = sum(len(s) for s in sites_by_pod.values())

    if dry_run:
        log.info("DRY RUN MODE — no sheet writes")

    # Auth check
    if not _check_and_refresh_auth():
        log.error("Cannot proceed without auth")
        return

    _set_timerange()
    log.info(f"Starting tracker run — {total_sites} sites")

    # 1. Exceptions (parcel-cli)
    from exception_tracker import run as track_exceptions
    log.info("--- Fetching exceptions ---")
    exceptions = track_exceptions(sites_by_pod)

    # 2. Return Bin (Trino)
    from return_bin_tracker import run as track_return_bins
    log.info("--- Fetching return bins (Trino) ---")
    return_bins = track_return_bins()

    # 3. LFR (parcel-cli batch list)
    from lfr_tracker import run as track_lfr
    log.info("--- Fetching LFR ---")
    lfr = track_lfr(sites_by_pod)

    # 4. Small Batches (parcel-cli batch list)
    from small_batch_tracker import run as track_small_batches
    log.info("--- Fetching small batches ---")
    small_batches = track_small_batches(sites_by_pod)

    # Summary
    log.info("=" * 60)
    log.info(f"{'Site':>8} | {'Replan':>6} | {'RetBin':>6} | {'LFR45':>5} | {'SmBat':>5}")
    log.info("-" * 60)
    exc_by_site = {r["site"]: r for r in exceptions}
    rb_by_site = {r["site"]: r.get("scan_return_bin", 0) for r in return_bins}
    lfr_by_site = {r["site"]: r for r in lfr}
    for pod, sites in sites_by_pod.items():
        for site in sites:
            replan = exc_by_site.get(site, {}).get("needs_replan", 0)
            rb = rb_by_site.get(site, 0)
            lfr_count = lfr_by_site.get(site, {}).get("lfr_over_45", 0)
            sb = small_batches.get(site, 0)
            if replan or rb or lfr_count or sb:
                log.info(f"{site:>8} | {replan:>6} | {rb:>6} | {lfr_count:>5} | {sb:>5}")
    log.info("=" * 60)

    # Write to sheet
    from tracker_writer import TrackerWriter
    writer = TrackerWriter(dry_run=dry_run)
    writer.write_all(exceptions, return_bins, lfr, small_batches)

    log.info("Done.")


if __name__ == "__main__":
    main()
