#!/usr/bin/env python3
"""
Daily snapshot — saves the day's timeline events and final scorecard values
to a new dated tab in the Google Sheet. Run at end of day (7 PM ET).

Usage:
  python daily_snapshot.py
  python daily_snapshot.py --dry-run
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import requests

log = logging.getLogger("snapshot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

ET = ZoneInfo("America/New_York")
DASHBOARD_URL = "http://127.0.0.1:5000"
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"
SPREADSHEET_ID = "1_2ONbHrqaTUjHP18MNMW6pcmsq_JOXtJv6Boiwm5CRw"
TIMELINE_FILE = Path(__file__).parent / "state" / "timeline_today.json"


def get_scorecard_data() -> list[dict]:
    """Fetch current scorecard data from localhost dashboard."""
    try:
        resp = requests.post(f"{DASHBOARD_URL}/api/scorecard/refresh", timeout=600)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        log.error(f"Failed to fetch scorecard: {e}")
        return []


def get_timeline() -> dict:
    """Load today's timeline from state file."""
    if TIMELINE_FILE.exists():
        data = json.loads(TIMELINE_FILE.read_text())
        if data.get("date") == str(date.today()):
            return data.get("sites", {})
    return {}


def save_snapshot(dry_run: bool = False):
    today_str = date.today().strftime("%Y-%m-%d")
    tab_name = today_str

    log.info(f"Building daily snapshot for {today_str}...")

    # Get data
    scorecard = get_scorecard_data()
    timeline = get_timeline()

    if not scorecard:
        log.error("No scorecard data — is the dashboard running?")
        return

    # Build scorecard summary rows
    summary_headers = [
        "Site", "Region", "CET Met", "CET Total",
        "Sort Scan Start", "Dispatch Start",
        "Needs Replan", "Return Bin", "LFR > 45m", "PLIB", "Small Batches",
    ]
    summary_rows = [summary_headers]
    for r in scorecard:
        summary_rows.append([
            r.get("site", ""),
            r.get("pod", ""),
            r.get("cet_met", 0),
            r.get("cet_total", 0),
            r.get("scan_start", ""),
            r.get("dispatch_start", ""),
            r.get("needs_replan", 0),
            r.get("return_bin", 0),
            r.get("lfr_over_45", 0),
            r.get("plib", 0),
            r.get("small_batches", 0),
        ])

    # Build timeline event rows
    event_headers = ["Time", "Site", "Region", "Event", "Value"]
    event_rows = [event_headers]
    for site, site_data in sorted(timeline.items()):
        pod = site_data.get("pod", "")
        for evt in site_data.get("events", []):
            event_rows.append([
                evt.get("time", ""),
                site,
                pod,
                evt.get("label", ""),
                evt.get("value", ""),
            ])

    if dry_run:
        log.info(f"[dry-run] Would create tab '{tab_name}'")
        log.info(f"  Scorecard: {len(summary_rows) - 1} sites")
        log.info(f"  Timeline: {len(event_rows) - 1} events")
        for row in event_rows[1:5]:
            log.info(f"    {row}")
        return

    # Write to sheet
    gc = gspread.oauth(
        credentials_filename=str(CREDENTIALS_FILE),
        authorized_user_filename=str(TOKEN_FILE),
    )
    ss = gc.open_by_key(SPREADSHEET_ID)

    # Create or clear the tab
    try:
        ws = ss.worksheet(tab_name)
        ws.clear()
        log.info(f"Tab '{tab_name}' exists — cleared")
    except gspread.exceptions.WorksheetNotFound:
        total_rows = len(summary_rows) + len(event_rows) + 5
        ws = ss.add_worksheet(title=tab_name, rows=max(total_rows, 100), cols=15)
        log.info(f"Created tab '{tab_name}'")

    # Write scorecard section
    ws.update(values=[["DAILY SCORECARD"]], range_name="A1")
    ws.update(values=summary_rows, range_name="A2")

    # Write timeline section below scorecard
    gap_row = len(summary_rows) + 3
    ws.update(values=[["TIMELINE EVENTS"]], range_name=f"A{gap_row}")
    ws.update(values=event_rows, range_name=f"A{gap_row + 1}")

    log.info(f"Snapshot saved: {len(summary_rows) - 1} sites, {len(event_rows) - 1} events → tab '{tab_name}'")


def main():
    parser = argparse.ArgumentParser(description="Save daily snapshot to Google Sheet")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    save_snapshot(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
