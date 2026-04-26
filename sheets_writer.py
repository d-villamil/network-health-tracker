"""
Google Sheets writer for parcel-cli exception tracking.
Append-only model — each run adds rows to the Exceptions tab.
"""

import logging
from pathlib import Path

import gspread
import yaml

log = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent / "config.yaml"
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"

# Column mapping for Exceptions tab
# A=Timestamp, B=Date, C=Site, D=Pod, E=Needs Replan, F=Missing,
# G=Delivery Hold, H=Total, I=Total Parcels, J=Exception Rate, K=Delta, L=Alert Sent
HEADERS = [
    "Timestamp", "Date", "Site", "Pod", "Needs Replan", "Missing",
    "Delivery Hold", "Total Exceptions", "Total Parcels", "Exception Rate",
    "Delta vs Prior", "Alert Sent",
]


SHIPMENT_HEADERS = [
    "Timestamp", "Shipment ID", "Carrier", "Origin", "Destination",
    "CET", "Actual Dropoff", "Late?", "Stop Exceptions",
]

TRACKED_HEADERS = [
    "Timestamp", "Site", "Region", "Quantity", "Exception Type", "Submitted By", "Notes",
]


class SheetsWriter:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._gc = None
        self._spreadsheet = None
        cfg = yaml.safe_load(CONFIG_FILE.read_text())
        self._sheet_id = cfg["sheets"]["spreadsheet_id"]
        self._exceptions_tab = cfg["sheets"]["exceptions_tab"]
        self._shipments_tab = cfg["sheets"]["shipments_tab"]
        self._tracked_tab = cfg["sheets"].get("tracked_actions_tab", "Tracked Actions")

    def _client(self):
        if self._gc is None:
            self._gc = gspread.oauth(
                credentials_filename=str(CREDENTIALS_FILE),
                authorized_user_filename=str(TOKEN_FILE),
            )
        return self._gc

    def _get_spreadsheet(self):
        if self._spreadsheet is None:
            self._spreadsheet = self._client().open_by_key(self._sheet_id)
        return self._spreadsheet

    def _get_or_create_tab(self, tab_name: str, headers: list[str]):
        """Get a worksheet tab, creating it with headers if it doesn't exist."""
        ss = self._get_spreadsheet()
        try:
            ws = ss.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = ss.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
            ws.append_row(headers, value_input_option="RAW")
            log.info(f"Created new tab '{tab_name}' with headers")
        return ws

    def write_exceptions(self, rows: list[dict]) -> None:
        """Append exception rows to the Exceptions tab."""
        if not rows:
            log.info("No exception rows to write")
            return

        sheet_rows = []
        for r in rows:
            sheet_rows.append([
                r["timestamp"],
                r["date"],
                r["site"],
                r["pod"],
                r["needs_replan"],
                r["missing"],
                r["delivery_hold"],
                r["total"],
                r["total_parcels"],
                f"{r['exception_rate']:.2%}" if isinstance(r["exception_rate"], float) else r["exception_rate"],
                r["delta"],
                r["alert_sent"],
            ])

        if self.dry_run:
            log.info(f"[dry-run] Would append {len(sheet_rows)} rows to '{self._exceptions_tab}'")
            for sr in sheet_rows[:5]:
                log.info(f"  {sr[2]:>8} | replan={sr[4]} missing={sr[5]} hold={sr[6]} total={sr[7]} delta={sr[10]:+d}")
            if len(sheet_rows) > 5:
                log.info(f"  ... and {len(sheet_rows) - 5} more rows")
            return

        if not self._sheet_id:
            log.error("No spreadsheet_id configured in config.yaml — cannot write")
            return

        ws = self._get_or_create_tab(self._exceptions_tab, HEADERS)
        ws.append_rows(sheet_rows, value_input_option="USER_ENTERED")
        log.info(f"Appended {len(sheet_rows)} rows to '{self._exceptions_tab}'")

    def write_shipments(self, rows: list[dict]) -> None:
        """Append late/exception shipment rows to the Shipments tab."""
        if not rows:
            log.info("No shipment rows to write")
            return

        sheet_rows = []
        for r in rows:
            sheet_rows.append([
                r["timestamp"],
                r["shipment_id"],
                r["carrier"],
                r["origin"],
                r["destination"],
                r["cet"],
                r["actual_dropoff"],
                r["late"],
                r["stop_exceptions"],
            ])

        if self.dry_run:
            log.info(f"[dry-run] Would append {len(sheet_rows)} rows to '{self._shipments_tab}'")
            for sr in sheet_rows[:5]:
                log.info(f"  {sr[1]} | {sr[2]:>15} | {sr[3]:>8} -> {sr[4]:>8} | late={sr[7]}")
            if len(sheet_rows) > 5:
                log.info(f"  ... and {len(sheet_rows) - 5} more rows")
            return

        if not self._sheet_id:
            log.error("No spreadsheet_id configured in config.yaml — cannot write")
            return

        ws = self._get_or_create_tab(self._shipments_tab, SHIPMENT_HEADERS)
        ws.append_rows(sheet_rows, value_input_option="USER_ENTERED")
        log.info(f"Appended {len(sheet_rows)} rows to '{self._shipments_tab}'")

    def write_tracked_action(self, row: dict) -> None:
        """Write a single tracked action row to the Tracked Actions tab."""
        if not self._sheet_id:
            log.error("No spreadsheet_id configured — cannot write tracked action")
            return

        ws = self._get_or_create_tab(self._tracked_tab, TRACKED_HEADERS)
        ws.append_row([
            row.get("timestamp", ""),
            row.get("site", ""),
            row.get("pod", ""),
            row.get("quantity", 0),
            row.get("action", ""),
            row.get("analyst", ""),
            row.get("notes", ""),
        ], value_input_option="USER_ENTERED")
        log.info(f"Tracked action written: {row['site']}")
