"""
Tracker sheet writer — updates fixed site rows in-place (like live-ops-monitor).
Reads column B for site→row mapping, then updates columns C-G.
"""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread

log = logging.getLogger(__name__)

CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"

SPREADSHEET_ID = "1cQH7gwBvAmZO8WiYNPbrCSnO8zxB0DUDhzaeMM5o-ZU"
SHEET_NAME = "Tracker"
SITE_COL = 2       # Column B — site codes
HEADER_ROW = 1     # Row 1 is header, data starts at row 2

COL_NEEDS_REPLAN = 3   # Column C
COL_RETURN_BIN = 4     # Column D
COL_LFR_OVER_45 = 5    # Column E
COL_SMALL_BATCHES = 6  # Column F
COL_LAST_UPDATED = 7   # Column G

ET = ZoneInfo("America/New_York")


class TrackerWriter:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._gc = None
        self._sheet = None
        self._site_row_map = None

    def _client(self):
        if self._gc is None:
            self._gc = gspread.oauth(
                credentials_filename=str(CREDENTIALS_FILE),
                authorized_user_filename=str(TOKEN_FILE),
            )
        return self._gc

    def _get_sheet(self):
        if self._sheet is None:
            self._sheet = self._client().open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        return self._sheet

    def _get_site_row_map(self) -> dict[str, int]:
        if self._site_row_map is not None:
            return self._site_row_map

        sheet = self._get_sheet()
        col_b = sheet.col_values(SITE_COL)
        site_map = {}
        for idx, val in enumerate(col_b):
            row_num = idx + 1
            if row_num <= HEADER_ROW or not val or not val.strip():
                continue
            site_map[val.strip()] = row_num

        self._site_row_map = site_map
        log.info(f"Site row map loaded: {len(site_map)} sites")
        return site_map

    def write_all(self, exceptions: list[dict], return_bins: list[dict],
                  lfr: list[dict], small_batches: dict[str, int]) -> None:
        """Update all columns for all sites in one batch."""
        site_map = self._get_site_row_map()
        ts = datetime.now(ET).strftime("%-I:%M %p ET")

        # Build lookup dicts
        exc_by_site = {r["site"]: r for r in exceptions}
        rb_by_site = {r["site"]: r.get("scan_return_bin", 0) for r in return_bins}
        lfr_by_site = {r["site"]: r for r in lfr}

        updates = []
        for site, row in site_map.items():
            # Needs Replan
            exc = exc_by_site.get(site, {})
            replan = exc.get("needs_replan", 0)
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row, COL_NEEDS_REPLAN),
                "values": [[replan]],
            })

            # Return Bin
            rb = rb_by_site.get(site, 0)
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row, COL_RETURN_BIN),
                "values": [[rb]],
            })

            # LFR > 45 min
            lfr_data = lfr_by_site.get(site, {})
            lfr_count = lfr_data.get("lfr_over_45", 0)
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row, COL_LFR_OVER_45),
                "values": [[lfr_count]],
            })

            # Small Batches
            sb = small_batches.get(site, 0)
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row, COL_SMALL_BATCHES),
                "values": [[sb]],
            })

            # Last Updated
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row, COL_LAST_UPDATED),
                "values": [[ts]],
            })

        if self.dry_run:
            log.info(f"[dry-run] Would update {len(updates)} cells across {len(site_map)} sites")
            for site in list(site_map.keys())[:5]:
                exc = exc_by_site.get(site, {})
                log.info(f"  {site}: replan={exc.get('needs_replan',0)} rb={rb_by_site.get(site,0)} "
                         f"lfr={lfr_by_site.get(site,{}).get('lfr_over_45',0)} sb={small_batches.get(site,0)}")
            return

        sheet = self._get_sheet()
        sheet.batch_update(updates)
        log.info(f"Tracker updated: {len(site_map)} sites, timestamp {ts}")
