#!/usr/bin/env python3
"""
parcel-cli automation runner.
Orchestrates exception tracking, sheet writes, and outreach.

Usage:
  python runner.py --once --mode exceptions --dry-run
  python runner.py --once --mode exceptions
  python runner.py --once --mode all
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

log = logging.getLogger("parcel-cli")


def setup_logging():
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "parcel-cli.log"),
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


def get_sites_by_pod(cfg: dict) -> dict[str, list[str]]:
    """Build pod -> site list from config."""
    return cfg.get("pods", {})


def run_exceptions(cfg: dict, dry_run: bool):
    """Run exception tracking across all sites."""
    from exception_tracker import run as track_exceptions
    from sheets_writer import SheetsWriter

    sites_by_pod = get_sites_by_pod(cfg)
    total_sites = sum(len(s) for s in sites_by_pod.values())
    log.info(f"Starting exception scan — {total_sites} sites across {len(sites_by_pod)} pods")

    results = track_exceptions(sites_by_pod)

    if not results:
        log.warning("No results — all sites failed or returned no data")
        return

    # Summary table
    log.info("=" * 70)
    log.info(f"{'Site':>8} | {'Pod':>5} | {'Replan':>6} | {'Returned':>8} | {'Total':>5} | {'Delta':>6}")
    log.info("-" * 70)
    for r in sorted(results, key=lambda x: x["needs_replan"], reverse=True):
        log.info(
            f"{r['site']:>8} | {r['pod']:>5} | {r['needs_replan']:>6} | "
            f"{r['returned_by_runner']:>8} | {r['total']:>5} | {r['delta']:>+6d}"
        )
    log.info("=" * 70)

    # Write to sheet
    writer = SheetsWriter(dry_run=dry_run)
    writer.write_exceptions(results)

    # Evaluate outreach thresholds and send Slack drafts
    from outreach_engine import evaluate, record_sent
    from slack_client import SlackClient

    outreach_cfg = cfg.get("outreach", {})
    actions = evaluate(results, outreach_cfg)

    if actions:
        slack = SlackClient(cfg.get("slack", {}), dry_run=dry_run)
        sent_sites = slack.send_outreach(actions)

        if sent_sites:
            record_sent(sent_sites)
            # Mark alert_sent in results for sheet accuracy
            sent_set = set(sent_sites)
            for r in results:
                if r["site"] in sent_set:
                    r["alert_sent"] = "Y"
    else:
        log.info("No outreach needed this run")


def run_shipments(cfg: dict, dry_run: bool):
    """Run shipment/CET check."""
    from shipment_checker import run as check_shipments
    from sheets_writer import SheetsWriter

    log.info("Starting shipment check...")
    results = check_shipments()

    if not results:
        log.info("No late or exception shipments found")
        return

    # Summary table
    log.info("=" * 90)
    log.info(f"{'Shipment ID':>18} | {'Carrier':>15} | {'Origin':>8} | {'Dest':>8} | {'Late':>4} | CET")
    log.info("-" * 90)
    for r in results:
        log.info(
            f"{r['shipment_id']:>18} | {r['carrier']:>15} | {r['origin']:>8} | "
            f"{r['destination']:>8} | {r['late']:>4} | {r['cet']}"
        )
    log.info("=" * 90)

    writer = SheetsWriter(dry_run=dry_run)
    writer.write_shipments(results)


def main():
    parser = argparse.ArgumentParser(description="parcel-cli automation runner")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--mode", choices=["exceptions", "shipments", "all"], default="all",
                        help="Which mode to run (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Log only, no writes")
    parser.add_argument("--config", type=str, help="Alternate config file path")
    args = parser.parse_args()

    setup_logging()

    cfg = load_config()
    dry_run = args.dry_run or cfg.get("dry_run", False)

    if dry_run:
        log.info("DRY RUN MODE — no sheet writes or Slack sends")

    # Set parcel-cli timerange to today — without this it uses the last-set date
    today = date.today()
    tomorrow = today + timedelta(days=1)
    result = subprocess.run(
        ["parcel-cli", "timerange", "--start", str(today), "--end", str(tomorrow)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        log.info(f"Timerange set: {today} to {tomorrow}")
    else:
        log.error(f"Failed to set timerange: {result.stderr.strip()}")
        return

    if args.mode in ("exceptions", "all"):
        run_exceptions(cfg, dry_run)

    if args.mode in ("shipments", "all"):
        run_shipments(cfg, dry_run)

    log.info("Done.")


if __name__ == "__main__":
    main()
